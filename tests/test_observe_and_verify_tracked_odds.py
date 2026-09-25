import base64
import json

from mystake.pipeline.notification_processor import NotificationProcessor
from mystake.pipeline.prematch_odds_tracker import PrematchOddsTracker
from mystake.registry.game_snapshot_registry import GameSnapshotRegistry
from mystake.sources.mqtt.message import MqttPublishMessage
from observe_and_verify_tracked_odds import (
    observe_update_game_ids,
    select_available_game_ids,
    verify_tracked_notifications,
)


class StopTest(Exception):
    """Raised by test doubles to end an otherwise-infinite bounded loop."""


class FakeMqttClient:
    def __init__(self, messages):
        self._messages = list(messages)

    def receive_publish(self) -> MqttPublishMessage:
        if not self._messages:
            raise StopTest

        item = self._messages.pop(0)

        if isinstance(item, BaseException):
            raise item

        return item


class FakeCacheClient:
    def __init__(self, payload_by_url):
        self._payload_by_url = payload_by_url

    def get(self, url: str) -> bytes:
        return self._payload_by_url[url]


class FakeHttpClient:
    def __init__(self, snapshots_by_game_id):
        self._snapshots_by_game_id = dict(snapshots_by_game_id)
        self.urls: list[str] = []

    def get_json(self, url: str):
        self.urls.append(url)
        game_id = int(url.rsplit("/", 1)[-1])
        return {
            "game": self._snapshots_by_game_id[game_id],
            "price": "[]",
            "disableMarkets": None,
        }


def game_payload(game_id, coef=1.8):
    return {"id": game_id, "ev": {"55": {"2001": {"coef": coef}}}}


def make_message(payload: bytes, topic: str = "prematch/games") -> MqttPublishMessage:
    return MqttPublishMessage(
        topic=topic, payload=payload, qos=0, retain=False, dup=False, packet_id=None
    )


def cache_encode(value) -> bytes:
    encoded = base64.b64encode(json.dumps(value).encode("utf-8")).decode("ascii")
    return json.dumps(encoded).encode("utf-8")


def make_tracker(game_ids, snapshots):
    http = FakeHttpClient(snapshots)
    registry = GameSnapshotRegistry(game_ids)
    tracker = PrematchOddsTracker(http, registry, min_request_interval_seconds=0)
    return tracker, http


# --- select_available_game_ids -----------------------------------------


def test_select_available_game_ids_filters_and_caps():
    observed = [1, 2, 3, 4, 5, 6]
    available = {2, 3, 4, 5, 6}

    selected = select_available_game_ids(
        observed, is_available=lambda gid: gid in available, max_tracked=3
    )

    assert selected == [2, 3, 4]


def test_select_available_game_ids_skips_unavailable():
    observed = [1, 2, 3]

    selected = select_available_game_ids(
        observed, is_available=lambda gid: gid != 2, max_tracked=5
    )

    assert selected == [1, 3]


# --- observe_update_game_ids ---------------------------------------------


def test_observe_stops_at_max_distinct_game_ids():
    url_a = "https://example.com/a"
    url_b = "https://example.com/b"

    cache = FakeCacheClient(
        {
            url_a: cache_encode({"UpdateList": [{"GameId": 111}], "DeleteList": []}),
            url_b: cache_encode({"UpdateList": [{"GameId": 222}], "DeleteList": []}),
        }
    )
    mqtt = FakeMqttClient(
        [
            make_message(f"cache:{url_a}".encode()),
            make_message(f"cache:{url_b}".encode()),
        ]
    )
    processor = NotificationProcessor(cache_client=cache)

    result = observe_update_game_ids(
        mqtt,
        processor,
        max_seconds=9999,
        max_notifications=9999,
        max_distinct_game_ids=2,
    )

    assert result.observed_game_ids == [111, 222]
    assert result.notifications_seen == 2
    assert result.stopped_reason == "max_distinct_game_ids_reached"


def test_observe_stops_at_max_notifications_when_no_new_ids():
    url_a = "https://example.com/a"

    cache = FakeCacheClient(
        {url_a: cache_encode({"UpdateList": [{"GameId": 111}], "DeleteList": []})}
    )
    mqtt = FakeMqttClient([make_message(f"cache:{url_a}".encode()) for _ in range(3)])
    processor = NotificationProcessor(cache_client=cache)

    result = observe_update_game_ids(
        mqtt,
        processor,
        max_seconds=9999,
        max_notifications=3,
        max_distinct_game_ids=5,
    )

    assert result.observed_game_ids == [111]
    assert result.notifications_seen == 3
    assert result.stopped_reason == "max_notifications_reached"


def test_observe_ignores_unrelated_topics():
    mqtt = FakeMqttClient([make_message(b"cache:whatever", topic="live/gamenew/1")])
    processor = NotificationProcessor(cache_client=FakeCacheClient({}))

    result = observe_update_game_ids(
        mqtt,
        processor,
        max_seconds=0.0,
        max_notifications=5,
        max_distinct_game_ids=5,
    )

    assert result.notifications_seen == 0
    assert result.observed_game_ids == []
    assert result.stopped_reason == "max_seconds_reached"


# --- verify_tracked_notifications -----------------------------------------


def test_verify_reports_real_tracked_match_and_price_change():
    tracker, http = make_tracker([1], {1: game_payload(1, coef=1.8)})
    http.set_game = lambda game_id, game: http._snapshots_by_game_id.__setitem__(
        game_id, game
    )

    # initial hydration outside the loop under test
    tracker.hydrate_all_tracked()
    http.set_game(1, game_payload(1, coef=2.0))

    url = "https://example.com/update"
    cache = FakeCacheClient(
        {url: cache_encode({"UpdateList": [{"GameId": 1}], "DeleteList": []})}
    )
    mqtt = FakeMqttClient([make_message(f"cache:{url}".encode())])
    processor = NotificationProcessor(cache_client=cache)

    result = verify_tracked_notifications(
        mqtt,
        tracker,
        processor,
        max_seconds=9999,
        max_notifications=9999,
    )

    assert result.tracked_match_notifications == 1
    assert result.fallback_revalidations == 0
    assert len(result.price_changes) == 1

    change = result.price_changes[0]
    assert change.game_id == 1
    assert change.market_id == "55"
    assert change.selection_id == "2001"
    assert change.previous_price == 1.8
    assert change.new_price == 2.0
    assert result.stopped_reason == "real_price_change_observed"


def test_verify_skips_notification_not_referencing_tracked_game():
    tracker, _http = make_tracker([1], {1: game_payload(1)})
    tracker.hydrate_all_tracked()

    url = "https://example.com/other"
    cache = FakeCacheClient(
        {url: cache_encode({"UpdateList": [{"GameId": 999}], "DeleteList": []})}
    )
    mqtt = FakeMqttClient([make_message(f"cache:{url}".encode())])
    processor = NotificationProcessor(cache_client=cache)

    result = verify_tracked_notifications(
        mqtt,
        tracker,
        processor,
        max_seconds=9999,
        max_notifications=1,
    )

    assert result.tracked_match_notifications == 0
    assert result.fallback_revalidations == 0
    assert result.price_changes == []
    assert result.stopped_reason == "max_notifications_reached"


def test_verify_falls_back_to_full_set_when_extraction_inconclusive():
    tracker, http = make_tracker([1, 2], {1: game_payload(1), 2: game_payload(2)})
    tracker.hydrate_all_tracked()
    http.urls.clear()

    mqtt = FakeMqttClient([make_message(b"opaque-trigger")])
    processor = NotificationProcessor(cache_client=FakeCacheClient({}))

    result = verify_tracked_notifications(
        mqtt,
        tracker,
        processor,
        max_seconds=9999,
        max_notifications=1,
    )

    assert result.tracked_match_notifications == 0
    assert result.fallback_revalidations == 1
    assert len(http.urls) == 2
