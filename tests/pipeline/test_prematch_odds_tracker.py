import threading
import time

from mystake.pipeline.prematch_odds_tracker import PrematchOddsTracker
from mystake.registry.game_snapshot_registry import GameSnapshotRegistry


class FakeHttpClient:
    """
    Fake `MystakeHttpClient` returning a canned `getprematchgamefull`
    outer payload per GameId, tracking call counts and concurrency.
    """

    def __init__(self, snapshots_by_game_id, *, gate: threading.Event | None = None):
        self._snapshots_by_game_id = snapshots_by_game_id
        self.gate = gate
        self.calls: list[str] = []
        self._lock = threading.Lock()
        self.max_concurrent_calls = 0
        self._current_concurrent_calls = 0

    def get_json(self, url: str):
        with self._lock:
            self.calls.append(url)
            self._current_concurrent_calls += 1
            self.max_concurrent_calls = max(
                self.max_concurrent_calls, self._current_concurrent_calls
            )

        if self.gate is not None:
            self.gate.wait(timeout=5)

        try:
            game_id = int(url.rsplit("/", 1)[-1])
            game = self._snapshots_by_game_id[game_id]
            return {"game": game, "price": "[]", "disableMarkets": None}
        finally:
            with self._lock:
                self._current_concurrent_calls -= 1


def game_payload(game_id, coef=1.8):
    return {"id": game_id, "ev": {"55": {"2001": {"coef": coef}}}}


def test_hydrate_one_produces_initial_outcome():
    http = FakeHttpClient({1: game_payload(1)})
    registry = GameSnapshotRegistry([1])
    tracker = PrematchOddsTracker(http, registry, min_request_interval_seconds=0)

    outcome = tracker.hydrate_one(1)

    assert outcome.is_initial is True
    assert outcome.snapshot.game_id == 1


def test_hydrate_many_isolates_multiple_games():
    http = FakeHttpClient({1: game_payload(1, coef=1.5), 2: game_payload(2, coef=2.5)})
    registry = GameSnapshotRegistry([1, 2])
    tracker = PrematchOddsTracker(
        http, registry, max_concurrency=2, min_request_interval_seconds=0
    )

    results = tracker.hydrate_many([1, 2])

    assert results[1].snapshot.game_id == 1
    assert results[2].snapshot.game_id == 2
    assert len(http.calls) == 2


def test_bounded_concurrency_is_respected():
    gate = threading.Event()
    http = FakeHttpClient(
        {i: game_payload(i) for i in range(1, 5)},
        gate=gate,
    )
    registry = GameSnapshotRegistry([1, 2, 3, 4])
    tracker = PrematchOddsTracker(
        http, registry, max_concurrency=2, min_request_interval_seconds=0
    )

    thread = threading.Thread(target=tracker.hydrate_many, args=([1, 2, 3, 4],))
    thread.start()

    time.sleep(0.2)
    gate.set()
    thread.join(timeout=5)

    assert http.max_concurrent_calls <= 2


def test_transient_http_failure_preserves_previous_snapshot():
    http = FakeHttpClient({1: game_payload(1)})
    registry = GameSnapshotRegistry([1])
    tracker = PrematchOddsTracker(http, registry, min_request_interval_seconds=0)

    tracker.hydrate_one(1)

    class FailingHttpClient:
        def get_json(self, url):
            raise RuntimeError("boom")

    tracker.hydrator.http_client = FailingHttpClient()

    outcome = tracker.hydrate_one(1)

    assert outcome.failed is True
    assert outcome.snapshot.game_id == 1
    assert registry.get(1).snapshot is not None


def test_concurrent_fetch_for_same_game_is_coalesced_not_duplicated():
    gate = threading.Event()
    http = FakeHttpClient({1: game_payload(1)}, gate=gate)
    registry = GameSnapshotRegistry([1])
    tracker = PrematchOddsTracker(http, registry, min_request_interval_seconds=0)

    first_thread = threading.Thread(target=tracker.hydrate_one, args=(1,))
    first_thread.start()

    # Wait for the first fetch to actually be in flight before attempting
    # the "concurrent" second one.
    for _ in range(50):
        if http.calls:
            break
        time.sleep(0.02)

    second_result = tracker.hydrate_one(1)

    gate.set()
    first_thread.join(timeout=5)

    # The second call found a fetch already in flight and coalesced
    # instead of issuing its own request.
    assert second_result is None
    assert len(http.calls) == 1
    assert registry.get(1).snapshot is not None


def test_min_request_interval_paces_requests():
    http = FakeHttpClient({1: game_payload(1)})
    registry = GameSnapshotRegistry([1])
    tracker = PrematchOddsTracker(
        http, registry, max_concurrency=1, min_request_interval_seconds=0.1
    )

    start = time.monotonic()
    tracker.hydrate_one(1)
    tracker.hydrate_one(1)
    elapsed = time.monotonic() - start

    assert elapsed >= 0.1
