from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor

from mystake.config import (
    PREMATCH_HYDRATION_MAX_CONCURRENCY,
    PREMATCH_HYDRATION_MIN_REQUEST_INTERVAL_SECONDS,
)
from mystake.pipeline.prematch_snapshot_hydration import PrematchSnapshotHydrator
from mystake.registry.game_snapshot_registry import FetchOutcome, GameSnapshotRegistry
from mystake.sources.http.client import MystakeHttpClient

logger = logging.getLogger(__name__)


class PrematchOddsTracker:
    """
    Bounded orchestrator that hydrates a fixed, small set of tracked
    GameIds against `getprematchgamefull` and applies each result to a
    `GameSnapshotRegistry`.

    Bounds (AGENTS.md sections 3/4 - no unbounded fan-out, rate limit
    requests):

    - `max_concurrency` caps the number of simultaneous HTTP requests
      across the tracked set (default conservative: 2). It never
      spawns a persistent thread per GameId - workers are transient,
      created only for the duration of one `hydrate_many` call.
    - `min_request_interval_seconds` enforces a minimum spacing between
      HTTP requests leaving this process, across all workers.
    - A per-GameId lock prevents two overlapping fetches for the same
      GameId; a call that finds one already in flight is coalesced -
      it does not issue a second request, and instead marks that
      GameId pending so the next `hydrate_many`/`hydrate_one` call
      still revalidates it exactly once, bounding pending work rather
      than growing a queue.
    """

    def __init__(
        self,
        http_client: MystakeHttpClient,
        registry: GameSnapshotRegistry,
        *,
        max_concurrency: int = PREMATCH_HYDRATION_MAX_CONCURRENCY,
        min_request_interval_seconds: float = (
            PREMATCH_HYDRATION_MIN_REQUEST_INTERVAL_SECONDS
        ),
    ) -> None:
        self.registry = registry
        self.hydrator = PrematchSnapshotHydrator(http_client)
        self.max_concurrency = max(1, max_concurrency)
        self.min_request_interval_seconds = min_request_interval_seconds

        self._in_flight: set[int | str] = set()
        self._pending: set[int | str] = set()
        self._state_lock = threading.Lock()
        self._pacing_lock = threading.Lock()
        self._last_request_at: float | None = None

    def hydrate_one(self, game_id: int | str) -> FetchOutcome | None:
        """
        Fetch and apply exactly one GameId. Returns `None` (without
        making a request) if a fetch for this GameId is already in
        flight - the caller's notification/attempt is coalesced into
        `_pending` and is not lost (see `_drain_pending`).
        """
        with self._state_lock:
            if game_id in self._in_flight:
                self._pending.add(game_id)
                logger.info(
                    "Fetch already in flight for game_id=%s; coalescing", game_id
                )
                return None

            self._in_flight.add(game_id)

        try:
            self._pace()
            snapshot = self.hydrator.fetch(game_id)
            return self.registry.apply_fetch_result(game_id, snapshot)
        finally:
            with self._state_lock:
                self._in_flight.discard(game_id)

    def hydrate_many(
        self,
        game_ids: Iterable[int | str],
    ) -> dict[int | str, FetchOutcome | None]:
        """
        Hydrate multiple GameIds with bounded concurrency
        (`max_concurrency` simultaneous requests), then drain any
        GameId coalesced into `_pending` during this batch exactly
        once more.
        """
        game_ids = tuple(dict.fromkeys(game_ids))

        results = self._hydrate_batch(game_ids)
        results.update(self._drain_pending())

        return results

    def hydrate_all_tracked(self) -> dict[int | str, FetchOutcome | None]:
        return self.hydrate_many(self.registry.tracked_game_ids())

    def _hydrate_batch(
        self,
        game_ids: tuple[int | str, ...],
    ) -> dict[int | str, FetchOutcome | None]:
        if not game_ids:
            return {}

        with ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            futures = {
                game_id: pool.submit(self.hydrate_one, game_id) for game_id in game_ids
            }

            return {game_id: future.result() for game_id, future in futures.items()}

    def _drain_pending(self) -> dict[int | str, FetchOutcome | None]:
        with self._state_lock:
            pending = tuple(self._pending)
            self._pending.clear()

        if not pending:
            return {}

        logger.info("Draining %s pending coalesced revalidation(s)", len(pending))

        return self._hydrate_batch(pending)

    def _pace(self) -> None:
        with self._pacing_lock:
            now = time.monotonic()

            if self._last_request_at is not None:
                elapsed = now - self._last_request_at
                remaining = self.min_request_interval_seconds - elapsed

                if remaining > 0:
                    time.sleep(remaining)

            self._last_request_at = time.monotonic()
