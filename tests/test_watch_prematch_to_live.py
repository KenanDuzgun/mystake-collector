import base64
import json
from unittest.mock import Mock

import pytest

from mystake.pipeline.live_odds_dispatcher import LiveOddsDispatcher
from mystake.pipeline.notification_processor import NotificationProcessor
from mystake.pipeline.prematch_to_live_handoff import HandoffTickResult
from mystake.registry.live_game_registry import LiveGameRegistry
from mystake.sources.mqtt.message import MqttPublishMessage
from watch_prematch_to_live import run, schedule_ticks


class StopTest(Exception):
    """Raised by test doubles to end an otherwise-infinite `run()` loop."""


class FakeCacheClient:
    def __init__(self, payload_by_url):
        self._payload_by_url = dict(payload_by_url)

    def get(self, url: str) -> bytes:
        payload = self._payload_by_url[url]

        if isinstance(payload, Exception):
            raise payload

        return payload

    def close(self) -> None:
        pass


def wrap_cache_payload(payload: dict) -> bytes:
    raw_json = json.dumps(payload).encode("utf-8")
    encoded = base64.b64encode(raw_json).decode("ascii")
    return json.dumps(encoded).encode("utf-8")


class FakeMqttClient:
    def __init__(self, messages):
        self._messages = list(messages)
        self.subscribed_topics: list[str] = []
        self.closed = False
        self._shutdown = False

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown

    def request_shutdown(self) -> None:
        self._shutdown = True

    def connect_with_retry(self) -> None:
        pass

    def subscribe(self, topic: str, qos: int = 0) -> int:
        self.subscribed_topics.append(topic)
        return 1

    def unsubscribe(self, topic: str) -> int:
        return 1

    def receive_publish(self) -> MqttPublishMessage:
        if not self._messages:
            raise StopTest

        item = self._messages.pop(0)

        if isinstance(item, BaseException):
            raise item

        return item

    def close(self) -> None:
        self.closed = True


def make_message(topic: str, cache_url: str) -> MqttPublishMessage:
    return MqttPublishMessage(
        topic=topic,
        payload=f"cache:{cache_url}".encode(),
        qos=0,
        retain=False,
        dup=False,
        packet_id=None,
    )


def test_run_dispatches_live_notification_for_dynamically_added_game():
    """
    Reproduces the "continuous live tracking" leg of the handoff
    pipeline in isolation: a GameId already added to the live registry
    (as `PrematchToLiveHandoffCoordinator._attempt_handoff` would do)
    receives ordinary PUBLISH notifications exactly like any other
    tracked live GameId.
    """
    registry = LiveGameRegistry(tracked_game_ids=())
    registry.add_game(1)

    cache_client = FakeCacheClient(
        {
            "cache-url-1": wrap_cache_payload(
                {"Match": {"GameID": 1, "Score": "0:0"}, "gmk": []}
            )
        }
    )
    dispatcher = LiveOddsDispatcher(registry, NotificationProcessor(cache_client))

    mqtt_client = FakeMqttClient([make_message("live/gamenew/1", "cache-url-1")])
    coordinator = Mock()
    coordinator.tick.return_value = HandoffTickResult(
        prematch_refresh_ok=True,
        live_refresh_ok=True,
        newly_detected=(),
        handed_off=(),
        handoff_failed=(),
        cleaned_up=(),
        prematch_outcomes={},
    )

    with pytest.raises(StopTest):
        run(
            mqtt_client,
            dispatcher,
            coordinator,
            {},
            observe_seconds=999,
            tick_interval_seconds=0,
            debug=False,
        )

    assert registry.get(1).notification_count == 1
    assert registry.get(1).snapshot is not None


def test_schedule_ticks_disabled_returns_none_for_nonpositive_interval():
    mqtt_client = FakeMqttClient([])
    coordinator = Mock()

    timer = schedule_ticks(coordinator, mqtt_client, interval_seconds=0, debug=False)

    assert timer is None
    coordinator.tick.assert_not_called()


def test_schedule_ticks_skips_when_shutdown_already_requested():
    mqtt_client = FakeMqttClient([])
    mqtt_client.request_shutdown()
    coordinator = Mock()

    timer = schedule_ticks(coordinator, mqtt_client, interval_seconds=30, debug=False)

    assert timer is None
    coordinator.tick.assert_not_called()


def test_scheduled_tick_calls_coordinator_and_does_not_reschedule_after_shutdown():
    mqtt_client = FakeMqttClient([])

    def tick_and_request_shutdown():
        # simulates "shutdown requested during transition" (Step 5):
        # the in-flight tick still completes, but no further tick is
        # scheduled afterwards
        mqtt_client.request_shutdown()
        return HandoffTickResult(
            prematch_refresh_ok=True,
            live_refresh_ok=True,
            newly_detected=(1,),
            handed_off=(1,),
            handoff_failed=(),
            cleaned_up=(),
            prematch_outcomes={},
        )

    coordinator = Mock()
    coordinator.tick.side_effect = tick_and_request_shutdown

    timer = schedule_ticks(coordinator, mqtt_client, interval_seconds=0.01, debug=False)
    assert timer is not None

    timer.join(timeout=2)

    coordinator.tick.assert_called_once()
    assert mqtt_client.shutdown_requested is True
