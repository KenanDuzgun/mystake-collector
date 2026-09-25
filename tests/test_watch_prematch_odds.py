import signal
from unittest.mock import Mock

import pytest

from mystake.config import MQTT_TOPIC_PREMATCH_GAMES
from mystake.pipeline.prematch_games_notification import (
    PrematchGamesRevalidationHandler,
)
from mystake.pipeline.prematch_odds_tracker import PrematchOddsTracker
from mystake.registry.game_snapshot_registry import GameSnapshotRegistry
from mystake.sources.mqtt.client import ListenerShutdown, MystakeMqttClient
from mystake.sources.mqtt.message import MqttPublishMessage
from watch_prematch_odds import install_shutdown_signal_handlers, run, serve


class StopTest(Exception):
    """Raised by test doubles to end an otherwise-infinite `run()` loop."""


class FakeHttpClient:
    def __init__(self, snapshots_by_game_id):
        self._snapshots_by_game_id = dict(snapshots_by_game_id)
        self.urls: list[str] = []
        self.closed = False
        self._fail_from_call: int | None = None

    def get_json(self, url: str):
        self.urls.append(url)
        call_index = len(self.urls)

        if self._fail_from_call is not None and call_index >= self._fail_from_call:
            raise RuntimeError("boom")

        game_id = int(url.rsplit("/", 1)[-1])
        game = self._snapshots_by_game_id[game_id]

        if isinstance(game, Exception):
            raise game

        return {"game": game, "price": "[]", "disableMarkets": None}

    def close(self) -> None:
        self.closed = True

    def set_game(self, game_id, game):
        self._snapshots_by_game_id[game_id] = game

    def fail_from_next_call(self) -> None:
        self._fail_from_call = len(self.urls) + 1


class FakeCacheClient:
    def __init__(self):
        self.closed = False

    def get(self, url: str) -> bytes:
        raise AssertionError("cache client should not be used in this test")

    def close(self) -> None:
        self.closed = True


class FakeMqttClient:
    def __init__(self, messages, *, subscribe_error: Exception | None = None):
        self._messages = list(messages)
        self._subscribe_error = subscribe_error
        self.connect_calls = 0
        self.subscribed_topics: list[str] = []
        self.closed = False

    def connect_with_retry(self) -> None:
        self.connect_calls += 1

    def subscribe(self, topic: str, qos: int = 0) -> int:
        if self._subscribe_error is not None:
            raise self._subscribe_error

        self.subscribed_topics.append(topic)
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


def make_message(payload: bytes, topic: str = MQTT_TOPIC_PREMATCH_GAMES):
    return MqttPublishMessage(
        topic=topic, payload=payload, qos=0, retain=False, dup=False, packet_id=None
    )


def game_payload(game_id, coef=1.8):
    return {"id": game_id, "ev": {"55": {"2001": {"coef": coef}}}}


def make_tracker(game_ids, snapshots):
    http = FakeHttpClient(snapshots)
    registry = GameSnapshotRegistry(game_ids)
    tracker = PrematchOddsTracker(http, registry, min_request_interval_seconds=0)
    handler = PrematchGamesRevalidationHandler(tracker=tracker)
    return tracker, handler, http


def test_initial_hydration_connects_subscribes_and_populates_registry():
    tracker, handler, _http = make_tracker([1], {1: game_payload(1)})
    mqtt_client = FakeMqttClient([])

    with pytest.raises(StopTest):
        run(mqtt_client, tracker, handler, debug=False)

    assert mqtt_client.connect_calls == 1
    assert mqtt_client.subscribed_topics == [MQTT_TOPIC_PREMATCH_GAMES]
    assert tracker.registry.get(1).snapshot is not None


def test_multiple_tracked_games_hydrated_independently():
    tracker, handler, http = make_tracker(
        [1, 2], {1: game_payload(1, coef=1.5), 2: game_payload(2, coef=2.5)}
    )
    mqtt_client = FakeMqttClient([])

    with pytest.raises(StopTest):
        run(mqtt_client, tracker, handler, debug=False)

    assert tracker.registry.get(1).snapshot.game_id == 1
    assert tracker.registry.get(2).snapshot.game_id == 2
    assert len(http.urls) == 2


def test_publish_triggers_revalidation_and_reports_price_change():
    tracker, handler, http = make_tracker([1], {1: game_payload(1, coef=1.8)})
    mqtt_client = FakeMqttClient([make_message(b"trigger-1")])

    http.set_game(1, game_payload(1, coef=2.0))

    with pytest.raises(StopTest):
        run(mqtt_client, tracker, handler, debug=False)

    assert len(http.urls) == 2
    snapshot = tracker.registry.get(1).snapshot
    assert snapshot.markets[0].selections[0].price == 2.0


def test_duplicate_notification_does_not_trigger_second_http_call():
    tracker, handler, http = make_tracker([1], {1: game_payload(1)})
    mqtt_client = FakeMqttClient(
        [make_message(b"same-payload"), make_message(b"same-payload")]
    )

    with pytest.raises(StopTest):
        run(mqtt_client, tracker, handler, debug=False)

    # One for initial hydration, one for the first (non-duplicate)
    # notification; the second, identical notification adds none.
    assert len(http.urls) == 2


def test_transient_http_failure_preserves_previous_snapshot():
    tracker, handler, http = make_tracker([1], {1: game_payload(1)})
    mqtt_client = FakeMqttClient([make_message(b"trigger-1")])

    # Call 1 (initial hydration) succeeds; call 2 (notification-triggered
    # revalidation) fails - the previously valid snapshot must survive.
    http._fail_from_call = 2

    with pytest.raises(StopTest):
        run(mqtt_client, tracker, handler, debug=False)

    assert tracker.registry.get(1).snapshot is not None
    assert tracker.registry.get(1).snapshot.game_id == 1


def test_non_prematch_games_publish_is_ignored():
    tracker, handler, http = make_tracker([1], {1: game_payload(1)})
    mqtt_client = FakeMqttClient(
        [make_message(b"cache:whatever", topic="live/gamenew/123")]
    )

    with pytest.raises(StopTest):
        run(mqtt_client, tracker, handler, debug=False)

    assert len(http.urls) == 1


def test_subscription_rejection_propagates_and_is_not_swallowed():
    tracker, handler, _http = make_tracker([1], {1: game_payload(1)})
    mqtt_client = FakeMqttClient(
        [], subscribe_error=RuntimeError("MQTT subscription rejected")
    )

    with pytest.raises(RuntimeError, match="rejected"):
        run(mqtt_client, tracker, handler, debug=False)


def test_serve_closes_resources_on_keyboard_interrupt():
    tracker, handler, _http = make_tracker([1], {1: game_payload(1)})
    mqtt_client = FakeMqttClient([KeyboardInterrupt()])
    http_client = FakeHttpClient({1: game_payload(1)})
    cache_client = FakeCacheClient()

    serve(mqtt_client, tracker, handler, http_client, cache_client, debug=False)

    assert mqtt_client.closed is True
    assert http_client.closed is True
    assert cache_client.closed is True


def test_serve_closes_resources_on_listener_shutdown():
    tracker, handler, _http = make_tracker([1], {1: game_payload(1)})
    mqtt_client = FakeMqttClient([ListenerShutdown("shutdown requested")])
    http_client = FakeHttpClient({1: game_payload(1)})
    cache_client = FakeCacheClient()

    serve(mqtt_client, tracker, handler, http_client, cache_client, debug=False)

    assert mqtt_client.closed is True
    assert http_client.closed is True
    assert cache_client.closed is True


def test_reconnect_and_resubscribe_then_still_processes_notification(monkeypatch):
    client = MystakeMqttClient()
    tracker, handler, http = make_tracker([1], {1: game_payload(1)})

    monkeypatch.setattr(client, "connect_with_retry", lambda: None)

    def fake_subscribe(topic: str, qos: int = 0) -> int:
        client._subscriptions[topic] = qos
        return 1

    monkeypatch.setattr(client, "subscribe", fake_subscribe)

    reconnect_calls = []

    def fake_reconnect() -> None:
        reconnect_calls.append(1)

    monkeypatch.setattr(client, "_reconnect_and_resubscribe", fake_reconnect)

    publish_packet = b"\x30\x17\x00\x0eprematch/gamestrigger"

    calls = 0

    def fake_receive_raw() -> bytes:
        nonlocal calls
        calls += 1

        if calls == 1:
            raise ConnectionError("simulated disconnect")

        if calls == 2:
            return publish_packet

        raise StopTest

    monkeypatch.setattr(client, "receive_raw", fake_receive_raw)

    with pytest.raises(StopTest):
        run(client, tracker, handler, debug=False)

    assert reconnect_calls == [1]
    assert len(http.urls) >= 1


def test_install_shutdown_signal_handlers_calls_request_shutdown(monkeypatch):
    client = MystakeMqttClient()

    registered = {}

    def fake_signal(signum, handler):
        registered[signum] = handler

    monkeypatch.setattr(signal, "signal", fake_signal)

    install_shutdown_signal_handlers(client)

    assert signal.SIGINT in registered
    assert signal.SIGTERM in registered
    assert client.shutdown_requested is False

    registered[signal.SIGINT](signal.SIGINT, None)

    assert client.shutdown_requested is True


def test_run_stops_when_shutdown_requested_while_receive_is_blocked(monkeypatch):
    client = MystakeMqttClient()
    tracker, handler, _http = make_tracker([1], {1: game_payload(1)})

    monkeypatch.setattr(client, "connect_with_retry", lambda: None)

    def fake_subscribe(topic: str, qos: int = 0) -> int:
        client._subscriptions[topic] = qos
        return 1

    monkeypatch.setattr(client, "subscribe", fake_subscribe)

    def fake_receive_raw() -> bytes:
        client.request_shutdown()
        raise ConnectionError("socket forcibly shut down by request_shutdown")

    monkeypatch.setattr(client, "receive_raw", fake_receive_raw)

    reconnect = Mock()
    monkeypatch.setattr(client, "_reconnect_and_resubscribe", reconnect)

    with pytest.raises(ListenerShutdown):
        run(client, tracker, handler, debug=False)

    reconnect.assert_not_called()
