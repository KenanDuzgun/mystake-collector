import base64
import json

from mystake.config import PREMATCH_GETHEADER_URL
from mystake.pipeline.live_discovery import LiveFixtureDiscovery
from mystake.pipeline.prematch_discovery import PrematchFixtureDiscovery
from mystake.pipeline.prematch_odds_tracker import PrematchOddsTracker
from mystake.pipeline.prematch_to_live_handoff import (
    HandoffPhase,
    PrematchToLiveHandoffCoordinator,
)
from mystake.registry.game_snapshot_registry import GameSnapshotRegistry
from mystake.registry.live_game_registry import LiveGameRegistry


def prematch_header_payload(game_ids):
    return {
        "Sports": [
            {
                "ID": 1,
                "Name": "Soccer",
                "Regions": [
                    {
                        "ID": 10,
                        "Name": "England",
                        "Champs": [
                            {
                                "ID": 100,
                                "Name": "Premier League",
                                "GameSmallItems": [
                                    {"ID": game_id, "StartTime": 1}
                                    for game_id in game_ids
                                ],
                            },
                        ],
                    },
                ],
            },
        ],
    }


def live_header_payload(game_ids):
    return {
        "Games": [{"ID": game_id, "Sport": "Soccer"} for game_id in game_ids],
        "Sports": [],
        "Regions": [],
        "Championats": [],
        "Teams": [],
        "mk": [],
    }


def wrap_cache_payload(payload: dict) -> bytes:
    raw_json = json.dumps(payload).encode("utf-8")
    encoded = base64.b64encode(raw_json).decode("ascii")
    return json.dumps(encoded).encode("utf-8")


def gamefull_outer(game_id):
    return {
        "game": json.dumps(
            {"id": game_id, "t1": 1, "t2": 2, "mc": 0, "pc": 0, "ev": {}}
        ),
        "price": "[]",
        "disableMarkets": None,
    }


class FakeHttpClient:
    """
    Dispatches on URL: `getheader/en` requests are served from a queue
    (one entry per expected `tick()` call), `getprematchgamefull`
    requests are served per-GameId from a fixed map (queue of
    responses per GameId, so a later call can differ from an earlier
    one - e.g. failure then success).
    """

    def __init__(self, header_payload_queue, gamefull_payloads_by_game):
        self._header_queue = list(header_payload_queue)
        self._gamefull = {
            game_id: list(responses)
            for game_id, responses in gamefull_payloads_by_game.items()
        }
        self.requested_urls: list[str] = []

    def get_json(self, url: str):
        self.requested_urls.append(url)

        if url == PREMATCH_GETHEADER_URL:
            response = self._header_queue.pop(0)
        else:
            game_id = int(url.rsplit("/", 1)[-1])
            responses = self._gamefull[game_id]
            response = responses.pop(0) if len(responses) > 1 else responses[0]

        if isinstance(response, Exception):
            raise response

        return response

    def close(self) -> None:
        pass


class FakeCacheClient:
    def __init__(self, live_header_payload_queue):
        self._queue = list(live_header_payload_queue)
        self.requested_urls: list[str] = []

    def get(self, url: str) -> bytes:
        self.requested_urls.append(url)
        response = self._queue.pop(0)

        if isinstance(response, Exception):
            raise response

        return response

    def close(self) -> None:
        pass


class FakeMqttClient:
    def __init__(self, subscribe_error_once_for=None):
        self.subscribed_topics: list[str] = []
        self._subscribe_error_once_for = dict(subscribe_error_once_for or {})
        self.shutdown_requested = False

    def subscribe(self, topic: str, qos: int = 0) -> int:
        game_id = topic.rsplit("/", 1)[-1]

        if game_id in self._subscribe_error_once_for:
            error = self._subscribe_error_once_for.pop(game_id)
            raise error

        self.subscribed_topics.append(topic)
        return 1


def build_coordinator(
    *,
    candidate_game_ids,
    header_payload_queue,
    gamefull_payloads_by_game,
    live_header_payload_queue,
    mqtt_client=None,
):
    http_client = FakeHttpClient(header_payload_queue, gamefull_payloads_by_game)
    cache_client = FakeCacheClient(live_header_payload_queue)

    prematch_discovery = PrematchFixtureDiscovery(http_client=http_client)
    live_discovery = LiveFixtureDiscovery(cache_client=cache_client)

    prematch_registry = GameSnapshotRegistry(tracked_game_ids=candidate_game_ids)
    prematch_tracker = PrematchOddsTracker(
        http_client,
        prematch_registry,
        max_concurrency=2,
        min_request_interval_seconds=0,
    )

    live_registry = LiveGameRegistry(tracked_game_ids=())
    mqtt_client = mqtt_client if mqtt_client is not None else FakeMqttClient()

    coordinator = PrematchToLiveHandoffCoordinator(
        prematch_discovery=prematch_discovery,
        live_discovery=live_discovery,
        prematch_tracker=prematch_tracker,
        live_registry=live_registry,
        mqtt_client=mqtt_client,
        candidate_game_ids=candidate_game_ids,
    )

    return coordinator, live_registry, mqtt_client, prematch_registry


def test_same_game_id_transition_is_detected_and_handed_off():
    coordinator, live_registry, mqtt_client, _ = build_coordinator(
        candidate_game_ids=[1],
        header_payload_queue=[
            prematch_header_payload([1]),
            prematch_header_payload([1]),
        ],
        gamefull_payloads_by_game={1: [gamefull_outer(1)]},
        live_header_payload_queue=[
            wrap_cache_payload(live_header_payload([])),
            wrap_cache_payload(live_header_payload([1])),
        ],
    )

    first = coordinator.tick()
    assert first.newly_detected == ()
    assert first.handed_off == ()
    assert coordinator.records()[1].phase is HandoffPhase.PREMATCH_TRACKING

    second = coordinator.tick()

    assert second.newly_detected == (1,)
    assert second.handed_off == (1,)
    assert coordinator.records()[1].phase is HandoffPhase.LIVE_HANDED_OFF
    assert "live/gamenew/1" in mqtt_client.subscribed_topics
    assert 1 in live_registry.tracked_game_ids()


def test_no_transition_for_unrelated_game_id():
    coordinator, live_registry, mqtt_client, _ = build_coordinator(
        candidate_game_ids=[1],
        header_payload_queue=[prematch_header_payload([1])],
        gamefull_payloads_by_game={1: [gamefull_outer(1)]},
        live_header_payload_queue=[wrap_cache_payload(live_header_payload([999]))],
    )

    result = coordinator.tick()

    assert result.newly_detected == ()
    assert result.handed_off == ()
    assert coordinator.records()[1].phase is HandoffPhase.PREMATCH_TRACKING
    assert mqtt_client.subscribed_topics == []
    assert live_registry.tracked_game_ids() == ()


def test_duplicate_live_observation_does_not_resubscribe():
    coordinator, _, mqtt_client, _ = build_coordinator(
        candidate_game_ids=[1],
        header_payload_queue=[
            prematch_header_payload([1]),
            prematch_header_payload([1]),
            prematch_header_payload([1]),
        ],
        gamefull_payloads_by_game={1: [gamefull_outer(1)]},
        live_header_payload_queue=[
            wrap_cache_payload(live_header_payload([1])),
            wrap_cache_payload(live_header_payload([1])),
            wrap_cache_payload(live_header_payload([1])),
        ],
    )

    coordinator.tick()
    coordinator.tick()
    coordinator.tick()

    assert mqtt_client.subscribed_topics == ["live/gamenew/1"]


def test_failed_subscribe_is_retryable_and_prematch_unaffected():
    mqtt_client = FakeMqttClient(subscribe_error_once_for={"1": RuntimeError("boom")})

    coordinator, live_registry, mqtt_client, prematch_registry = build_coordinator(
        candidate_game_ids=[1],
        header_payload_queue=[
            prematch_header_payload([1]),
            prematch_header_payload([1]),
        ],
        gamefull_payloads_by_game={1: [gamefull_outer(1)]},
        live_header_payload_queue=[
            wrap_cache_payload(live_header_payload([1])),
            wrap_cache_payload(live_header_payload([1])),
        ],
        mqtt_client=mqtt_client,
    )

    first = coordinator.tick()

    assert first.handoff_failed == (1,)
    assert coordinator.records()[1].phase is HandoffPhase.PREMATCH_TRACKING
    assert coordinator.records()[1].last_error is not None
    assert 1 not in live_registry.tracked_game_ids()
    # prematch tracking kept working despite the live handoff failure
    assert prematch_registry.get(1).snapshot is not None

    second = coordinator.tick()

    assert second.handed_off == (1,)
    assert coordinator.records()[1].phase is HandoffPhase.LIVE_HANDED_OFF


def test_prematch_and_live_overlap_before_cleanup():
    coordinator, live_registry, _, prematch_registry = build_coordinator(
        candidate_game_ids=[1],
        header_payload_queue=[
            prematch_header_payload([1]),
            prematch_header_payload([1]),
        ],
        gamefull_payloads_by_game={1: [gamefull_outer(1)]},
        live_header_payload_queue=[
            wrap_cache_payload(live_header_payload([1])),
            wrap_cache_payload(live_header_payload([1])),
        ],
    )

    coordinator.tick()
    result = coordinator.tick()

    # still present in getheader/en - no cleanup yet, both views coexist
    assert result.cleaned_up == ()
    assert coordinator.records()[1].phase is HandoffPhase.LIVE_HANDED_OFF
    assert 1 in live_registry.tracked_game_ids()
    assert prematch_registry.get(1).snapshot is not None


def test_live_snapshot_state_is_independent_of_prematch_snapshot():
    coordinator, live_registry, _, prematch_registry = build_coordinator(
        candidate_game_ids=[1],
        header_payload_queue=[
            prematch_header_payload([1]),
            prematch_header_payload([1]),
        ],
        gamefull_payloads_by_game={1: [gamefull_outer(1)]},
        live_header_payload_queue=[
            wrap_cache_payload(live_header_payload([])),
            wrap_cache_payload(live_header_payload([1])),
        ],
    )

    coordinator.tick()
    coordinator.tick()

    prematch_state = prematch_registry.get(1)
    live_state = live_registry.get(1)

    assert prematch_state.snapshot is not None
    assert len(prematch_state.snapshot.markets) >= 0
    # the live snapshot is never pre-populated from the prematch one -
    # it only arrives via a real live/gamenew PUBLISH, handled entirely
    # outside this coordinator
    assert live_state.snapshot is None


def test_prematch_cleanup_after_disappearance_from_getheader():
    coordinator, live_registry, _, _prematch_registry = build_coordinator(
        candidate_game_ids=[1],
        header_payload_queue=[
            prematch_header_payload([1]),
            prematch_header_payload([1]),
            prematch_header_payload([]),
        ],
        gamefull_payloads_by_game={1: [gamefull_outer(1)]},
        live_header_payload_queue=[
            wrap_cache_payload(live_header_payload([1])),
            wrap_cache_payload(live_header_payload([1])),
            wrap_cache_payload(live_header_payload([1])),
        ],
    )

    coordinator.tick()
    coordinator.tick()
    result = coordinator.tick()

    assert result.cleaned_up == (1,)
    assert coordinator.records()[1].phase is HandoffPhase.PREMATCH_CLEANED_UP
    # live tracking state is untouched by prematch cleanup
    assert 1 in live_registry.tracked_game_ids()


def test_cleanup_never_happens_before_successful_handoff():
    """
    A candidate that disappears from prematch discovery without ever
    having been observed live must not be "cleaned up" - there would
    be no live tracking to fall back on, so its prematch record is
    simply left as-is (not resurrected, not destroyed).
    """
    coordinator, live_registry, mqtt_client, _ = build_coordinator(
        candidate_game_ids=[1],
        header_payload_queue=[
            prematch_header_payload([1]),
            prematch_header_payload([]),
        ],
        gamefull_payloads_by_game={1: [gamefull_outer(1)]},
        live_header_payload_queue=[
            wrap_cache_payload(live_header_payload([])),
            wrap_cache_payload(live_header_payload([])),
        ],
    )

    coordinator.tick()
    result = coordinator.tick()

    assert result.cleaned_up == ()
    assert coordinator.records()[1].phase is HandoffPhase.PREMATCH_TRACKING
    assert mqtt_client.subscribed_topics == []
    assert live_registry.tracked_game_ids() == ()


def test_late_prematch_reappearance_does_not_resurrect_cleaned_up_candidate():
    coordinator, _, _, prematch_registry = build_coordinator(
        candidate_game_ids=[1],
        header_payload_queue=[
            prematch_header_payload([1]),
            prematch_header_payload([1]),
            prematch_header_payload([]),
            # a late/stale getheader response that (erroneously) still
            # lists the already-handed-off GameId
            prematch_header_payload([1]),
        ],
        gamefull_payloads_by_game={1: [gamefull_outer(1)]},
        live_header_payload_queue=[
            wrap_cache_payload(live_header_payload([1])),
            wrap_cache_payload(live_header_payload([1])),
            wrap_cache_payload(live_header_payload([1])),
            wrap_cache_payload(live_header_payload([1])),
        ],
    )

    coordinator.tick()
    coordinator.tick()
    cleanup_result = coordinator.tick()
    assert cleanup_result.cleaned_up == (1,)

    final_result = coordinator.tick()

    assert final_result.cleaned_up == ()
    assert coordinator.records()[1].phase is HandoffPhase.PREMATCH_CLEANED_UP
    # cleaned-up candidates are permanently excluded from further
    # prematch revalidation, so no new hydration outcome is produced
    assert 1 not in final_result.prematch_outcomes
    assert prematch_registry.get(1) is not None


def test_shutdown_requested_stops_further_handoff_attempts():
    coordinator, live_registry, mqtt_client, _ = build_coordinator(
        candidate_game_ids=[1],
        header_payload_queue=[prematch_header_payload([1])],
        gamefull_payloads_by_game={1: [gamefull_outer(1)]},
        live_header_payload_queue=[wrap_cache_payload(live_header_payload([1]))],
    )

    mqtt_client.shutdown_requested = True

    result = coordinator.tick()

    assert result.handed_off == ()
    assert mqtt_client.subscribed_topics == []
    assert coordinator.records()[1].phase is HandoffPhase.PREMATCH_TRACKING
    assert live_registry.tracked_game_ids() == ()


def test_per_game_isolation_only_live_candidate_is_handed_off():
    coordinator, live_registry, mqtt_client, prematch_registry = build_coordinator(
        candidate_game_ids=[1, 2],
        header_payload_queue=[prematch_header_payload([1, 2])],
        gamefull_payloads_by_game={
            1: [gamefull_outer(1)],
            2: [gamefull_outer(2)],
        },
        live_header_payload_queue=[wrap_cache_payload(live_header_payload([1]))],
    )

    result = coordinator.tick()

    assert result.handed_off == (1,)
    assert coordinator.records()[1].phase is HandoffPhase.LIVE_HANDED_OFF
    assert coordinator.records()[2].phase is HandoffPhase.PREMATCH_TRACKING
    assert 1 in live_registry.tracked_game_ids()
    assert 2 not in live_registry.tracked_game_ids()
    assert mqtt_client.subscribed_topics == ["live/gamenew/1"]
    # game 2's prematch tracking is untouched by game 1's handoff
    assert prematch_registry.get(2).snapshot is not None
