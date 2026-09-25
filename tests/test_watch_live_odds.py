import base64
import json
import signal

import pytest

from mystake.pipeline.live_odds_dispatcher import LiveOddsDispatcher
from mystake.pipeline.notification_processor import NotificationProcessor
from mystake.registry.live_game_registry import LiveGameRegistry
from mystake.sources.mqtt.client import ListenerShutdown, MystakeMqttClient
from mystake.sources.mqtt.message import MqttPublishMessage
from watch_live_odds import (
    install_shutdown_signal_handlers,
    reconcile_once,
    run,
    select_auto_game_ids,
    serve,
)


class StopTest(Exception):
    """Raised by test doubles to end an otherwise-infinite `run()` loop."""


class FakeCacheClient:
    def __init__(self, payload_by_url):
        self._payload_by_url = dict(payload_by_url)
        self.closed = False

    def get(self, url: str) -> bytes:
        payload = self._payload_by_url[url]

        if isinstance(payload, Exception):
            raise payload

        encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
        return json.dumps(encoded).encode("utf-8")

    def set_payload(self, url, payload):
        self._payload_by_url[url] = payload

    def close(self) -> None:
        self.closed = True


class FakeMqttClient:
    def __init__(self, messages, *, subscribe_error: Exception | None = None):
        self._messages = list(messages)
        self._subscribe_error = subscribe_error
        self.connect_calls = 0
        self.subscribed_topics: list[str] = []
        self.unsubscribed_topics: list[str] = []
        self.closed = False
        self._shutdown = False

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown

    def request_shutdown(self) -> None:
        self._shutdown = True

    def connect_with_retry(self) -> None:
        self.connect_calls += 1

    def subscribe(self, topic: str, qos: int = 0) -> int:
        if self._subscribe_error is not None:
            raise self._subscribe_error

        self.subscribed_topics.append(topic)
        return 1

    def unsubscribe(self, topic: str) -> int:
        self.unsubscribed_topics.append(topic)
        if topic in self.subscribed_topics:
            self.subscribed_topics.remove(topic)
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


def snapshot(game_id, score="0:0", gmk=None):
    return {
        "Match": {"GameID": game_id, "Score": score},
        "gmk": gmk if gmk is not None else [],
    }


def make_dispatcher(game_ids, cache_payloads):
    registry = LiveGameRegistry(game_ids)
    cache_client = FakeCacheClient(cache_payloads)
    dispatcher = LiveOddsDispatcher(registry, NotificationProcessor(cache_client))
    return dispatcher, cache_client


def make_dispatcher_with_mqtt(game_ids, cache_payloads, mqtt_client):
    registry = LiveGameRegistry(game_ids)
    cache_client = FakeCacheClient(cache_payloads)
    dispatcher = LiveOddsDispatcher(
        registry, NotificationProcessor(cache_client), mqtt_client=mqtt_client
    )
    return dispatcher, cache_client


def terminal_snapshot(game_id, score="2:1"):
    return {
        "Match": {
            "GameID": game_id,
            "Score": score,
            "Status": 3,
            "BetStatus": 0,
            "EventStatus": 40,
        },
        "gmk": [],
    }


def new_stats(game_ids):
    return {
        gid: {
            "notification_count": 0,
            "price_changes": 0,
            "score_changes": 0,
            "selection_changes": 0,
        }
        for gid in game_ids
    }


def test_subscribes_exact_topic_per_tracked_game():
    dispatcher, _cache = make_dispatcher([1, 2, 3], {})
    mqtt_client = FakeMqttClient([])
    stats = new_stats([1, 2, 3])

    with pytest.raises(StopTest):
        run(mqtt_client, dispatcher, (1, 2, 3), stats, observe_seconds=999, debug=False)

    assert mqtt_client.connect_calls == 1
    assert mqtt_client.subscribed_topics == [
        "live/gamenew/1",
        "live/gamenew/2",
        "live/gamenew/3",
    ]


def test_correct_game_dispatch_and_independent_snapshots():
    dispatcher, _cache = make_dispatcher(
        [1, 2],
        {
            "https://example.com/1": snapshot(1, score="0:0"),
            "https://example.com/2": snapshot(2, score="0:0"),
        },
    )
    mqtt_client = FakeMqttClient(
        [
            make_message("live/gamenew/1", "https://example.com/1"),
            make_message("live/gamenew/2", "https://example.com/2"),
        ]
    )
    stats = new_stats([1, 2])

    with pytest.raises(StopTest):
        run(mqtt_client, dispatcher, (1, 2), stats, observe_seconds=999, debug=False)

    assert dispatcher.registry.get(1).snapshot["Match"]["GameID"] == 1
    assert dispatcher.registry.get(2).snapshot["Match"]["GameID"] == 2


def test_price_change_reported_only_for_the_affected_game():
    dispatcher, _cache_client = make_dispatcher(
        [1, 2],
        {
            "https://example.com/1a": snapshot(
                1, gmk=[{"id": 10, "mid": 5, "v": 1.8, "visible": True}]
            ),
            "https://example.com/2a": snapshot(2),
            "https://example.com/1b": snapshot(
                1, gmk=[{"id": 10, "mid": 5, "v": 2.0, "visible": True}]
            ),
        },
    )
    mqtt_client = FakeMqttClient(
        [
            make_message("live/gamenew/1", "https://example.com/1a"),
            make_message("live/gamenew/2", "https://example.com/2a"),
            make_message("live/gamenew/1", "https://example.com/1b"),
        ]
    )
    stats = new_stats([1, 2])

    with pytest.raises(StopTest):
        run(mqtt_client, dispatcher, (1, 2), stats, observe_seconds=999, debug=False)

    assert stats[1]["price_changes"] == 1
    assert stats[2]["price_changes"] == 0


def test_duplicate_notification_is_not_double_counted_as_a_change():
    dispatcher, _cache = make_dispatcher([1], {"https://example.com/1": snapshot(1)})
    mqtt_client = FakeMqttClient(
        [
            make_message("live/gamenew/1", "https://example.com/1"),
            make_message("live/gamenew/1", "https://example.com/1"),
        ]
    )
    stats = new_stats([1])

    with pytest.raises(StopTest):
        run(mqtt_client, dispatcher, (1,), stats, observe_seconds=999, debug=False)

    assert dispatcher.registry.get(1).notification_count == 2
    assert stats[1]["price_changes"] == 0
    assert stats[1]["selection_changes"] == 0


def test_failed_fetch_for_one_game_does_not_stop_other_tracked_games():
    dispatcher, _cache_client = make_dispatcher(
        [1, 2],
        {
            "https://example.com/1": snapshot(1),
            "https://example.com/2": ConnectionError("transient"),
            "https://example.com/2b": snapshot(2),
        },
    )
    mqtt_client = FakeMqttClient(
        [
            make_message("live/gamenew/1", "https://example.com/1"),
            make_message("live/gamenew/2", "https://example.com/2"),
            make_message("live/gamenew/2", "https://example.com/2b"),
        ]
    )
    stats = new_stats([1, 2])

    with pytest.raises(StopTest):
        run(mqtt_client, dispatcher, (1, 2), stats, observe_seconds=999, debug=False)

    assert dispatcher.registry.get(1).snapshot is not None
    assert dispatcher.registry.get(2).snapshot is not None
    assert dispatcher.registry.get(2).last_error is None  # cleared by the later success


def test_unrelated_topic_publish_is_ignored():
    dispatcher, _cache = make_dispatcher([1], {})
    mqtt_client = FakeMqttClient(
        [make_message("prematch/games", "https://example.com/x")]
    )
    stats = new_stats([1])

    with pytest.raises(StopTest):
        run(mqtt_client, dispatcher, (1,), stats, observe_seconds=999, debug=False)

    assert dispatcher.registry.get(1).snapshot is None


def test_subscription_rejection_propagates_and_is_not_swallowed():
    dispatcher, _cache = make_dispatcher([1], {})
    mqtt_client = FakeMqttClient(
        [], subscribe_error=RuntimeError("MQTT subscription rejected")
    )
    stats = new_stats([1])

    with pytest.raises(RuntimeError, match="rejected"):
        run(mqtt_client, dispatcher, (1,), stats, observe_seconds=999, debug=False)


def test_no_subscription_after_shutdown_begins():
    dispatcher, _cache = make_dispatcher([1, 2], {})
    mqtt_client = FakeMqttClient([])
    stats = new_stats([1, 2])

    def connect_and_shutdown():
        mqtt_client.connect_calls += 1
        mqtt_client.request_shutdown()

    mqtt_client.connect_with_retry = connect_and_shutdown

    run(mqtt_client, dispatcher, (1, 2), stats, observe_seconds=999, debug=False)

    assert mqtt_client.subscribed_topics == []


def test_serve_closes_resources_on_keyboard_interrupt():
    dispatcher, cache_client = make_dispatcher([1], {})
    mqtt_client = FakeMqttClient([KeyboardInterrupt()])
    stats = new_stats([1])

    serve(
        mqtt_client,
        dispatcher,
        (1,),
        stats,
        cache_client,
        observe_seconds=999,
        debug=False,
    )

    assert mqtt_client.closed is True
    assert cache_client.closed is True


def test_serve_closes_resources_on_listener_shutdown():
    dispatcher, cache_client = make_dispatcher([1], {})
    mqtt_client = FakeMqttClient([ListenerShutdown("shutdown requested")])
    stats = new_stats([1])

    serve(
        mqtt_client,
        dispatcher,
        (1,),
        stats,
        cache_client,
        observe_seconds=999,
        debug=False,
    )

    assert mqtt_client.closed is True
    assert cache_client.closed is True


def test_reconnect_and_resubscribe_then_still_processes_notification(monkeypatch):
    client = MystakeMqttClient()
    dispatcher, _cache_client = make_dispatcher(
        [1], {"https://example.com/1": snapshot(1)}
    )
    stats = new_stats([1])

    monkeypatch.setattr(client, "connect_with_retry", lambda: None)

    def fake_subscribe(topic: str, qos: int = 0) -> int:
        client._subscriptions[topic] = qos
        return 1

    monkeypatch.setattr(client, "subscribe", fake_subscribe)

    reconnect_calls = []

    def fake_reconnect() -> None:
        reconnect_calls.append(1)

    monkeypatch.setattr(client, "_reconnect_and_resubscribe", fake_reconnect)

    topic_bytes = b"live/gamenew/1"
    payload_bytes = b"cache:https://example.com/1"
    variable_header = len(topic_bytes).to_bytes(2, byteorder="big") + topic_bytes
    remaining = variable_header + payload_bytes
    publish_packet = bytes([0x30, len(remaining)]) + remaining

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
        run(client, dispatcher, (1,), stats, observe_seconds=999, debug=False)

    assert reconnect_calls == [1]
    assert dispatcher.registry.get(1).snapshot is not None


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


class Fixture:
    def __init__(self, game_id, sport):
        self.game_id = game_id
        self.sport = sport


def test_select_auto_game_ids_prefers_distinct_sports_first():
    fixtures = (
        Fixture(1, "Soccer"),
        Fixture(2, "Soccer"),
        Fixture(3, "Tennis"),
        Fixture(4, "Basketball"),
    )

    selected = select_auto_game_ids(fixtures, max_games=3)

    assert selected == [1, 3, 4]


def test_select_auto_game_ids_bounded_by_max_games():
    fixtures = tuple(Fixture(i, "Soccer") for i in range(10))

    selected = select_auto_game_ids(fixtures, max_games=5)

    assert len(selected) == 5


def test_format_price_change_block_includes_names_and_line():
    from types import SimpleNamespace

    from mystake.pipeline.live_market_enrichment import SelectionMetadata
    from mystake.pipeline.live_snapshot_diff import SelectionPriceChange
    from watch_live_odds import format_price_change_block

    change = SelectionPriceChange(
        selection_id=2876337561,
        market_id=616,
        old=2.20,
        new=2.15,
    )
    metadata = SelectionMetadata(
        market_id=616,
        market_name="Total hometeam",
        selection_name="under",
        line=2.5,
    )
    fixture = SimpleNamespace(team1="Team A", team2="Team B")

    block = format_price_change_block(75832139, change, metadata, fixture)

    assert "GAME: 75832139" in block
    assert "FIXTURE: Team A vs Team B" in block
    assert "MARKET: 616 | Total hometeam" in block
    assert "SELECTION: 2876337561 | under" in block
    assert "LINE: 2.5" in block
    assert "PRICE: 2.2 -> 2.15" in block


def test_format_price_change_block_omits_line_when_absent():
    from mystake.pipeline.live_market_enrichment import SelectionMetadata
    from mystake.pipeline.live_snapshot_diff import SelectionPriceChange
    from watch_live_odds import format_price_change_block

    change = SelectionPriceChange(
        selection_id=2876337662,
        market_id=602,
        old=1.01,
        new=1.02,
    )
    metadata = SelectionMetadata(
        market_id=602,
        market_name="3way",
        selection_name="1",
        line=None,
    )

    block = format_price_change_block(75832139, change, metadata, None)

    assert "LINE:" not in block
    assert "FIXTURE: UNKNOWN" in block


def test_format_price_change_block_unknown_when_no_metadata():
    from mystake.pipeline.live_snapshot_diff import SelectionPriceChange
    from watch_live_odds import format_price_change_block

    change = SelectionPriceChange(
        selection_id=1,
        market_id=2,
        old=1.5,
        new=1.6,
    )

    block = format_price_change_block(1, change, None, None)

    assert "MARKET: 2 | UNKNOWN" in block
    assert "SELECTION: 1 | UNKNOWN" in block


# --- Phase 4D: lifecycle end-to-end through run() ---


def test_terminal_transition_unsubscribes_and_stops_further_processing():
    mqtt_client = FakeMqttClient(
        [
            make_message("live/gamenew/1", "https://example.com/1a"),
            make_message("live/gamenew/1", "https://example.com/1b"),
            make_message("live/gamenew/1", "https://example.com/1c"),
        ]
    )
    dispatcher, _cache = make_dispatcher_with_mqtt(
        [1],
        {
            "https://example.com/1a": snapshot(1, score="0:0"),
            "https://example.com/1b": terminal_snapshot(1, score="2:1"),
            # deliberately unregistered - a late PUBLISH must never reach
            # the cache client after finalization
        },
        mqtt_client,
    )
    stats = new_stats([1])

    with pytest.raises(StopTest):
        run(mqtt_client, dispatcher, (1,), stats, observe_seconds=999, debug=False)

    assert dispatcher.registry.is_finalized(1) is True
    assert dispatcher.registry.get(1).snapshot["Match"]["Score"] == "2:1"
    assert mqtt_client.unsubscribed_topics == ["live/gamenew/1"]


def test_finalized_game_does_not_affect_other_tracked_game_in_run_loop():
    mqtt_client = FakeMqttClient(
        [
            make_message("live/gamenew/1", "https://example.com/1a"),
            make_message("live/gamenew/1", "https://example.com/1b"),
            make_message("live/gamenew/2", "https://example.com/2a"),
        ]
    )
    dispatcher, _cache = make_dispatcher_with_mqtt(
        [1, 2],
        {
            "https://example.com/1a": snapshot(1, score="0:0"),
            "https://example.com/1b": terminal_snapshot(1, score="2:1"),
            "https://example.com/2a": snapshot(2, score="1:0"),
        },
        mqtt_client,
    )
    stats = new_stats([1, 2])

    with pytest.raises(StopTest):
        run(mqtt_client, dispatcher, (1, 2), stats, observe_seconds=999, debug=False)

    assert dispatcher.registry.is_finalized(1) is True
    assert dispatcher.registry.is_finalized(2) is False
    assert dispatcher.registry.get(2).snapshot["Match"]["Score"] == "1:0"
    assert mqtt_client.unsubscribed_topics == ["live/gamenew/1"]


class FakeDiscoveryRegistry:
    def __init__(self, fixtures):
        self._fixtures = tuple(fixtures)

    def list_all(self):
        return self._fixtures


class DiscoveryFixture:
    def __init__(self, game_id):
        self.game_id = game_id


class FakeDiscovery:
    def __init__(self, fixtures, *, fail=False):
        self.registry = FakeDiscoveryRegistry(fixtures)
        self._fail = fail
        self.refresh_calls = 0

    def refresh(self):
        self.refresh_calls += 1
        if self._fail:
            return None
        return object()


def test_reconcile_once_flags_missing_game_unknown_without_finalizing():
    from mystake.registry.live_game_registry import LiveLifecycleState

    registry = LiveGameRegistry([1, 2])
    discovery = FakeDiscovery([DiscoveryFixture(2)])

    result = reconcile_once(discovery, registry)

    assert result.newly_unknown == (1,)
    assert registry.get(1).lifecycle_state is LiveLifecycleState.UNKNOWN
    assert registry.get(1).is_finalized is False
    assert registry.get(2).lifecycle_state is LiveLifecycleState.ACTIVE


def test_reconcile_once_preserves_state_on_discovery_failure():
    from mystake.registry.live_game_registry import LiveLifecycleState

    registry = LiveGameRegistry([1])
    discovery = FakeDiscovery([], fail=True)

    result = reconcile_once(discovery, registry)

    assert result is None
    assert registry.get(1).lifecycle_state is LiveLifecycleState.ACTIVE
