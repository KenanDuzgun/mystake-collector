import base64
import json

from mystake.pipeline.live_odds_dispatcher import (
    LiveOddsDispatcher,
    extract_game_id_from_topic,
    topic_for_game_id,
)
from mystake.pipeline.notification_processor import NotificationProcessor
from mystake.registry.live_game_registry import LiveGameRegistry
from mystake.sources.mqtt.message import MqttPublishMessage


class FakeCacheClient:
    def __init__(self, payload_by_url):
        self._payload_by_url = payload_by_url

    def get(self, url: str) -> bytes:
        payload = self._payload_by_url[url]
        encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
        return json.dumps(encoded).encode("utf-8")


def make_message(topic: str, cache_url: str) -> MqttPublishMessage:
    return MqttPublishMessage(
        topic=topic,
        payload=f"cache:{cache_url}".encode(),
        qos=0,
        retain=False,
        dup=False,
        packet_id=None,
    )


def snapshot(game_id, score="0:0", status=None, bet_status=None, event_status=None):
    match = {"GameID": game_id, "Score": score}
    if status is not None:
        match["Status"] = status
    if bet_status is not None:
        match["BetStatus"] = bet_status
    if event_status is not None:
        match["EventStatus"] = event_status
    return {"Match": match, "gmk": []}


def terminal_snapshot(game_id, score="2:1"):
    return snapshot(game_id, score=score, status=3, bet_status=0, event_status=40)


class RecordingMqttClient:
    def __init__(self):
        self.unsubscribed_topics = []

    def unsubscribe(self, topic: str) -> int:
        self.unsubscribed_topics.append(topic)
        return 1


class FailingUnsubscribeMqttClient:
    def unsubscribe(self, topic: str) -> int:
        raise RuntimeError("UNSUBACK not received")


def test_topic_for_game_id_and_extraction_round_trip():
    topic = topic_for_game_id(75832139)

    assert topic == "live/gamenew/75832139"
    assert extract_game_id_from_topic(topic) == 75832139


def test_extract_game_id_from_topic_rejects_unrelated_topic():
    assert extract_game_id_from_topic("prematch/games") is None
    assert extract_game_id_from_topic("live/gamenew/") is None


def test_dispatch_routes_notification_to_correct_game():
    cache_client = FakeCacheClient(
        {
            "https://example.com/1": snapshot(1),
            "https://example.com/2": snapshot(2),
        }
    )
    registry = LiveGameRegistry([1, 2])
    dispatcher = LiveOddsDispatcher(registry, NotificationProcessor(cache_client))

    outcome = dispatcher.handle(make_message("live/gamenew/1", "https://example.com/1"))

    assert outcome.game_id == 1
    assert outcome.is_initial is True
    assert registry.get(1).snapshot is not None
    assert registry.get(2).snapshot is None


def test_notification_for_one_game_does_not_affect_other_tracked_game():
    cache_client = FakeCacheClient(
        {
            "https://example.com/1": snapshot(1, score="0:0"),
            "https://example.com/1b": snapshot(1, score="1:0"),
            "https://example.com/2": snapshot(2, score="0:0"),
        }
    )
    registry = LiveGameRegistry([1, 2])
    dispatcher = LiveOddsDispatcher(registry, NotificationProcessor(cache_client))

    dispatcher.handle(make_message("live/gamenew/1", "https://example.com/1"))
    dispatcher.handle(make_message("live/gamenew/2", "https://example.com/2"))
    dispatcher.handle(make_message("live/gamenew/1", "https://example.com/1b"))

    assert registry.get(1).snapshot["Match"]["Score"] == "1:0"
    assert registry.get(2).snapshot["Match"]["Score"] == "0:0"
    assert registry.get(2).notification_count == 1


def test_dispatch_ignores_untracked_game_topic():
    cache_client = FakeCacheClient({"https://example.com/9": snapshot(9)})
    registry = LiveGameRegistry([1])
    dispatcher = LiveOddsDispatcher(registry, NotificationProcessor(cache_client))

    outcome = dispatcher.handle(make_message("live/gamenew/9", "https://example.com/9"))

    assert outcome is None


def test_dispatch_ignores_unrelated_topic():
    cache_client = FakeCacheClient({})
    registry = LiveGameRegistry([1])
    dispatcher = LiveOddsDispatcher(registry, NotificationProcessor(cache_client))

    outcome = dispatcher.handle(make_message("prematch/games", "https://example.com/x"))

    assert outcome is None


def test_duplicate_notification_marked_duplicate_and_counted():
    cache_client = FakeCacheClient({"https://example.com/1": snapshot(1)})
    registry = LiveGameRegistry([1])
    dispatcher = LiveOddsDispatcher(registry, NotificationProcessor(cache_client))

    first = dispatcher.handle(make_message("live/gamenew/1", "https://example.com/1"))
    second = dispatcher.handle(make_message("live/gamenew/1", "https://example.com/1"))

    assert first.duplicate is False
    assert second.duplicate is True
    assert registry.get(1).notification_count == 2


class FailingCacheClient:
    def get(self, url: str) -> bytes:
        raise ConnectionError("transient failure")


def test_failed_fetch_preserves_previous_snapshot_and_isolates_game():
    registry = LiveGameRegistry([1, 2])
    registry.apply_notification(1, snapshot(1, score="0:0"))
    previous_snapshot = registry.get(1).snapshot

    dispatcher = LiveOddsDispatcher(
        registry, NotificationProcessor(FailingCacheClient())
    )

    outcome = dispatcher.handle(make_message("live/gamenew/1", "https://example.com/1"))

    assert outcome is None
    assert registry.get(1).snapshot is previous_snapshot
    assert registry.get(1).last_error is not None
    assert registry.get(2).last_error is None


class NonDictCacheClient:
    def get(self, url: str) -> bytes:
        return json.dumps(
            base64.b64encode(json.dumps([1, 2, 3]).encode("utf-8")).decode("ascii")
        ).encode("utf-8")


def test_non_dict_payload_preserves_previous_snapshot():
    registry = LiveGameRegistry([1])
    registry.apply_notification(1, snapshot(1))
    previous_snapshot = registry.get(1).snapshot

    dispatcher = LiveOddsDispatcher(
        registry, NotificationProcessor(NonDictCacheClient())
    )

    outcome = dispatcher.handle(make_message("live/gamenew/1", "https://example.com/1"))

    assert outcome is None
    assert registry.get(1).snapshot is previous_snapshot
    assert "not a dict" in registry.get(1).last_error


# --- Phase 4D: lifecycle management ---


def test_terminal_notification_finalizes_and_unsubscribes():
    cache_client = FakeCacheClient(
        {
            "https://example.com/1a": snapshot(
                1, status=1, bet_status=1, event_status=10
            ),
            "https://example.com/1b": terminal_snapshot(1),
        }
    )
    registry = LiveGameRegistry([1])
    mqtt_client = RecordingMqttClient()
    dispatcher = LiveOddsDispatcher(
        registry, NotificationProcessor(cache_client), mqtt_client=mqtt_client
    )

    dispatcher.handle(make_message("live/gamenew/1", "https://example.com/1a"))
    outcome = dispatcher.handle(
        make_message("live/gamenew/1", "https://example.com/1b")
    )

    assert outcome.became_terminal is True
    assert registry.is_finalized(1) is True
    assert mqtt_client.unsubscribed_topics == ["live/gamenew/1"]


def test_late_notification_after_finalization_is_ignored_without_fetch():
    cache_client = FakeCacheClient(
        {
            "https://example.com/1a": snapshot(
                1, status=1, bet_status=1, event_status=10
            ),
            "https://example.com/1b": terminal_snapshot(1),
        }
    )
    registry = LiveGameRegistry([1])
    mqtt_client = RecordingMqttClient()
    dispatcher = LiveOddsDispatcher(
        registry, NotificationProcessor(cache_client), mqtt_client=mqtt_client
    )
    dispatcher.handle(make_message("live/gamenew/1", "https://example.com/1a"))
    dispatcher.handle(make_message("live/gamenew/1", "https://example.com/1b"))
    final_snapshot = registry.get(1).snapshot

    # A late PUBLISH referencing a cache URL that was never registered in the
    # fake cache client - if the dispatcher tried to fetch it, this would
    # raise a KeyError. It must not even attempt the fetch.
    outcome = dispatcher.handle(
        make_message("live/gamenew/1", "https://example.com/1-not-registered")
    )

    assert outcome is None
    assert registry.get(1).snapshot is final_snapshot
    assert mqtt_client.unsubscribed_topics == ["live/gamenew/1"]


def test_finalization_without_mqtt_client_still_finalizes_registry():
    cache_client = FakeCacheClient(
        {
            "https://example.com/1a": snapshot(
                1, status=1, bet_status=1, event_status=10
            ),
            "https://example.com/1b": terminal_snapshot(1),
        }
    )
    registry = LiveGameRegistry([1])
    dispatcher = LiveOddsDispatcher(registry, NotificationProcessor(cache_client))

    dispatcher.handle(make_message("live/gamenew/1", "https://example.com/1a"))
    outcome = dispatcher.handle(
        make_message("live/gamenew/1", "https://example.com/1b")
    )

    assert outcome.became_terminal is True
    assert registry.is_finalized(1) is True


def test_unsubscribe_failure_does_not_prevent_finalization():
    cache_client = FakeCacheClient(
        {
            "https://example.com/1a": snapshot(
                1, status=1, bet_status=1, event_status=10
            ),
            "https://example.com/1b": terminal_snapshot(1),
        }
    )
    registry = LiveGameRegistry([1])
    dispatcher = LiveOddsDispatcher(
        registry,
        NotificationProcessor(cache_client),
        mqtt_client=FailingUnsubscribeMqttClient(),
    )

    dispatcher.handle(make_message("live/gamenew/1", "https://example.com/1a"))
    outcome = dispatcher.handle(
        make_message("live/gamenew/1", "https://example.com/1b")
    )

    assert outcome.became_terminal is True
    assert registry.is_finalized(1) is True


def test_finalization_of_one_game_leaves_other_game_active():
    cache_client = FakeCacheClient(
        {
            "https://example.com/1a": snapshot(
                1, status=1, bet_status=1, event_status=10
            ),
            "https://example.com/1b": terminal_snapshot(1),
            "https://example.com/2": snapshot(
                2, status=1, bet_status=1, event_status=10
            ),
        }
    )
    registry = LiveGameRegistry([1, 2])
    mqtt_client = RecordingMqttClient()
    dispatcher = LiveOddsDispatcher(
        registry, NotificationProcessor(cache_client), mqtt_client=mqtt_client
    )

    dispatcher.handle(make_message("live/gamenew/1", "https://example.com/1a"))
    dispatcher.handle(make_message("live/gamenew/1", "https://example.com/1b"))
    dispatcher.handle(make_message("live/gamenew/2", "https://example.com/2"))

    assert registry.is_finalized(1) is True
    assert registry.is_finalized(2) is False
    assert mqtt_client.unsubscribed_topics == ["live/gamenew/1"]
