from __future__ import annotations

import logging
from typing import Protocol

from mystake.config import MQTT_TOPIC_LIVE_GAME_PREFIX
from mystake.events.mapper import map_live_diff_to_events
from mystake.events.models import LiveDomainEvent, LiveEventType
from mystake.pipeline.notification_processor import NotificationProcessor
from mystake.registry.live_game_registry import LiveApplyOutcome, LiveGameRegistry
from mystake.sources.mqtt.message import MqttPublishMessage

logger = logging.getLogger(__name__)


def topic_for_game_id(game_id: int | str) -> str:
    return f"{MQTT_TOPIC_LIVE_GAME_PREFIX}{game_id}"


def extract_game_id_from_topic(topic: str) -> int | str | None:
    """
    Recovers the GameId from an exact `live/gamenew/{GameId}` topic.
    Returns `None` for any topic not matching this prefix (e.g. a
    stray/legacy subscription) rather than guessing.
    """
    if not topic.startswith(MQTT_TOPIC_LIVE_GAME_PREFIX):
        return None

    suffix = topic[len(MQTT_TOPIC_LIVE_GAME_PREFIX) :]

    if not suffix:
        return None

    try:
        return int(suffix)
    except ValueError:
        return suffix


class SupportsUnsubscribe(Protocol):
    def unsubscribe(self, topic: str) -> int: ...


class LiveOddsDispatcher:
    """
    Routes one real MQTT PUBLISH notification on an exact
    `live/gamenew/{GameId}` topic to that GameId's entry in a bounded
    `LiveGameRegistry`.

    A notification is only ever applied to the single tracked GameId
    its topic names - there is no shared/global revalidation step (no
    analog of `prematch/games`' broad, un-scoped signal is needed here,
    since the live topic itself is the exact per-game scope), so one
    game's update structurally cannot mutate another tracked game's
    state.

    Phase 4D: once a GameId's registry entry reaches the TERMINAL
    lifecycle state (a verified match-end transition), this dispatcher
    stops fetching/processing further notifications for it entirely
    (no cache fetch is even attempted) and, on the notification that
    caused the transition, unsubscribes the exact MQTT topic via the
    real MQTT client's existing unsubscribe support (reused as-is -
    see `mystake.sources.mqtt.client.MystakeMqttClient.unsubscribe`,
    which waits for a real UNSUBACK) so no further PUBLISH is even
    delivered for it.
    """

    def __init__(
        self,
        registry: LiveGameRegistry,
        notification_processor: NotificationProcessor,
        mqtt_client: SupportsUnsubscribe | None = None,
    ) -> None:
        self.registry = registry
        self.notification_processor = notification_processor
        self.mqtt_client = mqtt_client

    def handle(self, message: MqttPublishMessage) -> LiveApplyOutcome | None:
        game_id = extract_game_id_from_topic(message.topic)

        if game_id is None or game_id not in self.registry.tracked_game_ids():
            logger.debug(
                "Ignoring PUBLISH on untracked/unrelated topic=%s", message.topic
            )
            return None

        if self.registry.is_finalized(game_id):
            logger.info(
                "game_id=%s already finalized; ignoring late PUBLISH on topic=%s "
                "without fetching",
                game_id,
                message.topic,
            )
            return None

        try:
            result = self.notification_processor.process(message)
        except Exception as exc:
            logger.exception(
                "Failed to process live PUBLISH for game_id=%s; "
                "preserving previous snapshot",
                game_id,
            )
            self.registry.record_failure(game_id, error=str(exc))
            return None

        if not isinstance(result.data, dict):
            error = (
                f"decoded payload was not a dict (type={type(result.data).__name__})"
            )
            logger.warning(
                "game_id=%s: %s; preserving previous snapshot", game_id, error
            )
            self.registry.record_failure(game_id, error=error)
            return None

        outcome = self.registry.apply_notification(game_id, result.data)

        if outcome is not None and outcome.became_terminal:
            self._finalize(game_id, outcome)

        return outcome

    def _finalize(self, game_id: int | str, outcome: LiveApplyOutcome) -> None:
        """
        Runs exactly once per GameId, on the notification that caused
        the ACTIVE/UNKNOWN -> TERMINAL transition
        (`LiveApplyOutcome.became_terminal`, itself only ever `True`
        once per GameId - see `LiveGameRegistry.apply_notification`).
        """
        match_end_events = self._match_end_events(outcome)

        for event in match_end_events:
            logger.info(
                "MATCH_ENDED event game_id=%s payload=%s", game_id, event.payload
            )

        if self.mqtt_client is None:
            logger.warning(
                "game_id=%s finalized but no MQTT client was provided to the "
                "dispatcher; topic not unsubscribed (registry-level ignoring of "
                "further notifications still applies)",
                game_id,
            )
            return

        topic = topic_for_game_id(game_id)

        try:
            self.mqtt_client.unsubscribe(topic)
            logger.info(
                "Unsubscribed finalized game_id=%s topic=%s (UNSUBACK confirmed)",
                game_id,
                topic,
            )
        except Exception:
            logger.exception(
                "Failed to unsubscribe finalized game_id=%s topic=%s; "
                "the registry still ignores any further notification for it",
                game_id,
                topic,
            )

    @staticmethod
    def _match_end_events(
        outcome: LiveApplyOutcome,
    ) -> tuple[LiveDomainEvent, ...]:
        if outcome.diff is None:
            return ()

        events = map_live_diff_to_events(outcome.diff, outcome.snapshot)

        return tuple(
            event for event in events if event.event_type == LiveEventType.MATCH_ENDED
        )
