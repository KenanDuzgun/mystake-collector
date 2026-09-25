from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace

from mystake.models.snapshot import Snapshot
from mystake.pipeline.prematch_snapshot_diff import (
    PrematchSnapshotDiff,
    diff_prematch_snapshots,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrackedGameState:
    game_id: int | str
    snapshot: Snapshot | None
    last_fetched_at: float | None
    last_error: str | None


@dataclass(frozen=True)
class FetchOutcome:
    """
    Result of applying one hydration attempt to the registry.

    `failed=True` means the fetch/parse failed and the previous valid
    snapshot (if any) was preserved untouched - `diff` is always
    `None` in that case. `is_initial=True` means this is the first
    successful snapshot for this GameId (nothing to diff against yet).
    """

    game_id: int | str
    snapshot: Snapshot | None
    diff: PrematchSnapshotDiff | None
    is_initial: bool
    failed: bool


class GameSnapshotRegistry:
    """
    In-memory `game_id -> TrackedGameState` store for a bounded, fixed
    set of tracked GameIds.

    `apply_fetch_result` is the only mutator: a successful fetch
    atomically replaces the stored snapshot only after the new
    snapshot has already been parsed/validated by the caller (the
    hydrator); a failed fetch (`snapshot=None`) preserves the previous
    valid snapshot and records the error, per AGENTS.md section 5 (no
    silent data loss / no partial application on failure).
    """

    def __init__(self, tracked_game_ids: Iterable[int | str]) -> None:
        self._tracked_game_ids: tuple[int | str, ...] = tuple(
            dict.fromkeys(tracked_game_ids)
        )
        self._state: dict[int | str, TrackedGameState] = {
            game_id: TrackedGameState(
                game_id=game_id,
                snapshot=None,
                last_fetched_at=None,
                last_error=None,
            )
            for game_id in self._tracked_game_ids
        }

    def tracked_game_ids(self) -> tuple[int | str, ...]:
        return self._tracked_game_ids

    def get(self, game_id: int | str) -> TrackedGameState | None:
        return self._state.get(game_id)

    def apply_fetch_result(
        self,
        game_id: int | str,
        snapshot: Snapshot | None,
        *,
        error: str | None = None,
    ) -> FetchOutcome | None:
        if game_id not in self._state:
            logger.warning(
                "apply_fetch_result called for untracked game_id=%s; ignoring",
                game_id,
            )
            return None

        previous_state = self._state[game_id]

        if snapshot is None:
            self._state[game_id] = replace(
                previous_state,
                last_fetched_at=time.time(),
                last_error=error or "fetch failed",
            )
            logger.warning(
                "Preserving previous snapshot for game_id=%s after failed fetch",
                game_id,
            )
            return FetchOutcome(
                game_id=game_id,
                snapshot=previous_state.snapshot,
                diff=None,
                is_initial=False,
                failed=True,
            )

        is_initial = previous_state.snapshot is None

        diff = (
            None
            if is_initial
            else diff_prematch_snapshots(previous_state.snapshot, snapshot)
        )

        self._state[game_id] = TrackedGameState(
            game_id=game_id,
            snapshot=snapshot,
            last_fetched_at=time.time(),
            last_error=None,
        )

        return FetchOutcome(
            game_id=game_id,
            snapshot=snapshot,
            diff=diff,
            is_initial=is_initial,
            failed=False,
        )
