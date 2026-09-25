from mystake.pipeline.notification_processor import NotificationProcessor
from mystake.pipeline.prematch_games_notification import (
    PrematchGamesRevalidationHandler,
)
from mystake.pipeline.prematch_odds_tracker import PrematchOddsTracker
from mystake.registry.game_snapshot_registry import GameSnapshotRegistry
from mystake.sources.mqtt.message import MqttPublishMessage


class FakeHttpClient:
    def __init__(self, snapshots_by_game_id):
        self._snapshots_by_game_id = snapshots_by_game_id
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


def make_tracker(game_ids, snapshots):
    http = FakeHttpClient(snapshots)
    registry = GameSnapshotRegistry(game_ids)
    tracker = PrematchOddsTracker(http, registry, min_request_interval_seconds=0)
    return tracker, http


def test_notification_revalidates_full_tracked_set_when_payload_unidentifiable():
    tracker, http = make_tracker([1, 2], {1: game_payload(1), 2: game_payload(2)})
    handler = PrematchGamesRevalidationHandler(tracker=tracker)

    results = handler.handle(make_message(b"opaque-trigger"))

    assert set(results) == {1, 2}
    assert len(http.urls) == 2


def test_duplicate_notification_is_deduplicated():
    tracker, http = make_tracker([1], {1: game_payload(1)})
    handler = PrematchGamesRevalidationHandler(tracker=tracker)

    first = handler.handle(make_message(b"same-payload"))
    second = handler.handle(make_message(b"same-payload"))

    assert first != {}
    assert second == {}
    assert len(http.urls) == 1


def test_distinct_notifications_each_trigger_revalidation():
    tracker, http = make_tracker([1], {1: game_payload(1)})
    handler = PrematchGamesRevalidationHandler(tracker=tracker)

    handler.handle(make_message(b"payload-a"))
    handler.handle(make_message(b"payload-b"))

    assert len(http.urls) == 2


def test_update_list_narrows_revalidation_to_referenced_tracked_games():
    tracker, http = make_tracker(
        [1, 2, 3],
        {1: game_payload(1), 2: game_payload(2), 3: game_payload(3)},
    )

    class FakeCacheClient:
        def get(self, url):
            return b"irrelevant"

    class DecodingNotificationProcessor(NotificationProcessor):
        def process(self, message):
            from mystake.pipeline.notification_processor import ProcessedNotification

            return ProcessedNotification(
                topic=message.topic,
                cache_url="cache://x",
                data={"UpdateList": [{"GameId": 2}], "DeleteList": []},
            )

    handler = PrematchGamesRevalidationHandler(
        tracker=tracker,
        notification_processor=DecodingNotificationProcessor(
            cache_client=FakeCacheClient()
        ),
    )

    results = handler.handle(make_message(b"cache:whatever"))

    assert set(results) == {2}
    assert len(http.urls) == 1
    assert "prematchgamefull/28/2" in http.urls[0]


def test_update_list_referencing_no_tracked_game_skips_all_requests():
    tracker, http = make_tracker([1], {1: game_payload(1)})

    class FakeCacheClient:
        def get(self, url):
            return b"irrelevant"

    class DecodingNotificationProcessor(NotificationProcessor):
        def process(self, message):
            from mystake.pipeline.notification_processor import ProcessedNotification

            return ProcessedNotification(
                topic=message.topic,
                cache_url="cache://x",
                data={"UpdateList": [{"GameId": 999}]},
            )

    handler = PrematchGamesRevalidationHandler(
        tracker=tracker,
        notification_processor=DecodingNotificationProcessor(
            cache_client=FakeCacheClient()
        ),
    )

    results = handler.handle(make_message(b"cache:whatever"))

    assert results == {}
    assert http.urls == []


def test_unidentifiable_update_list_falls_back_to_full_tracked_set():
    tracker, _http = make_tracker([1, 2], {1: game_payload(1), 2: game_payload(2)})

    class FakeCacheClient:
        def get(self, url):
            return b"irrelevant"

    class DecodingNotificationProcessor(NotificationProcessor):
        def process(self, message):
            from mystake.pipeline.notification_processor import ProcessedNotification

            return ProcessedNotification(
                topic=message.topic,
                cache_url="cache://x",
                data={"SomethingElse": "unrecognized-shape"},
            )

    handler = PrematchGamesRevalidationHandler(
        tracker=tracker,
        notification_processor=DecodingNotificationProcessor(
            cache_client=FakeCacheClient()
        ),
    )

    results = handler.handle(make_message(b"cache:whatever"))

    assert set(results) == {1, 2}
