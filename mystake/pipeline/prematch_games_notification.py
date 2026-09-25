from __future__ import annotations

import logging
from typing import Any

from mystake.pipeline.notification_processor import NotificationProcessor
from mystake.pipeline.prematch_odds_tracker import PrematchOddsTracker
from mystake.registry.game_snapshot_registry import FetchOutcome
from mystake.sources.mqtt.message import MqttPublishMessage

logger = logging.getLogger(__name__)

_UNSET = object()

_GAME_ID_KEYS = ("GameId", "GameID", "Id")


class PrematchGamesRevalidationHandler:
    """
    Handles MQTT `prematch/games` notifications by triggering a bounded
    revalidation of the tracked GameId set via a `PrematchOddsTracker`.

    `prematch/games` is a broad/global revalidation signal (docs/handoff
    /handoff.md section 7, docs/product/SCHEMA.md section 3.3) - it does
    not guarantee any specific tracked GameId changed, and its
    `UpdateList`/`DeleteList` payload semantics are UNKNOWN. This
    handler therefore:

    - Deduplicates consecutive notifications with an identical decoded
      value (or raw bytes, if not cache-indirected), mirroring
      `PrematchHeaderRefreshHandler`.
    - Best-effort extracts GameIds from `UpdateList`/`DeleteList` *only*
      to narrow which tracked GameIds are revalidated when doing so is
      unambiguous (a list of dicts/ids); it never trusts the extracted
      set to be a complete delta, and falls back to revalidating the
      entire (already-bounded) tracked set whenever extraction is
      inconclusive.
    - Never fetches more than the tracked set, regardless of how many
      GameIds a notification's payload references.
    """

    def __init__(
        self,
        tracker: PrematchOddsTracker,
        notification_processor: NotificationProcessor | None = None,
    ) -> None:
        self.tracker = tracker
        self.notification_processor = notification_processor
        self._last_notification_value: Any = _UNSET

    def handle(
        self,
        message: MqttPublishMessage,
    ) -> dict[int | str, FetchOutcome | None]:
        value = self._decode_notification_value(message)

        if (
            value is not _UNSET
            and self._last_notification_value is not _UNSET
            and value == self._last_notification_value
        ):
            logger.info(
                "Duplicate prematch/games notification; skipping redundant revalidation"
            )
            return {}

        self._last_notification_value = value

        tracked_ids = self.tracker.registry.tracked_game_ids()
        relevant_ids = _extract_relevant_game_ids(value)

        if relevant_ids is not None:
            target_ids = tuple(
                game_id
                for game_id in tracked_ids
                if game_id in relevant_ids or str(game_id) in relevant_ids
            )

            if not target_ids:
                logger.info(
                    "prematch/games notification did not reference any "
                    "tracked GameId; skipping revalidation"
                )
                return {}
        else:
            target_ids = tracked_ids

        logger.info(
            "prematch/games notification triggering bounded revalidation game_ids=%s",
            target_ids,
        )

        return self.tracker.hydrate_many(target_ids)

    def _decode_notification_value(
        self,
        message: MqttPublishMessage,
    ) -> Any:
        if self.notification_processor is not None:
            try:
                return self.notification_processor.process(message).data
            except ValueError:
                pass

        return message.payload


def _extract_relevant_game_ids(value: Any) -> set[Any] | None:
    """
    Best-effort extraction of GameIds referenced by `UpdateList`/
    `DeleteList`. Returns `None` (inconclusive) unless at least one
    GameId-shaped entry was actually found, so callers fall back to
    bounded full-tracked-set revalidation rather than trusting an
    empty/unparsable result to mean "nothing changed".
    """
    if not isinstance(value, dict):
        return None

    game_ids: set[Any] = set()

    for key in ("UpdateList", "DeleteList"):
        entries = value.get(key)

        if not isinstance(entries, list):
            continue

        for entry in entries:
            if isinstance(entry, dict):
                for id_key in _GAME_ID_KEYS:
                    if id_key in entry:
                        game_ids.add(entry[id_key])
                        break
            elif isinstance(entry, (int, str)):
                game_ids.add(entry)

    return game_ids or None
