from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from mystake.pipeline.live_snapshot_diff import LiveSnapshotDiff, diff_live_snapshots

logger = logging.getLogger(__name__)


class LiveLifecycleState(StrEnum):
    """
    Per-GameId lifecycle state (Phase 4D).

    ACTIVE: tracked and, as far as observed evidence shows, still
        live. The default state for every tracked GameId.
    TERMINAL: a verified terminal transition (currently:
        `diff_live_snapshots`' `match_ended`, evaluated against real
        `Match.Status`/`BetStatus`/`EventStatus` - see
        docs/product/SCHEMA.md section 3) has been observed. Terminal
        is a one-way, permanent state - once set it is never reverted
        (AGENTS.md section 5: no silent/implicit reinterpretation of
        settled data).
    UNKNOWN: the GameId disappeared from the latest `live/headernew/en`
        discovery refresh without any verified terminal evidence
        (Phase 4D Step 5 - AGENTS.md explicitly forbids treating
        discovery-disappearance alone as match completion). Reversible:
        reappearing in a later discovery refresh, or any further real
        MQTT notification for the GameId, returns it to ACTIVE.
    """

    ACTIVE = "ACTIVE"
    TERMINAL = "TERMINAL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LiveTrackedGameState:
    game_id: int | str
    snapshot: dict[str, Any] | None
    last_notified_at: float | None
    last_error: str | None
    notification_count: int
    lifecycle_state: LiveLifecycleState = LiveLifecycleState.ACTIVE
    finalized_at: float | None = None
    missing_from_discovery_since: float | None = None
    missing_from_discovery_streak: int = 0

    @property
    def is_finalized(self) -> bool:
        return self.lifecycle_state is LiveLifecycleState.TERMINAL


@dataclass(frozen=True)
class LiveApplyOutcome:
    """
    Result of applying one real live notification to the registry.

    `is_initial=True` means this is the first snapshot received for
    this GameId (nothing to diff against yet - `diff=None`).
    `duplicate=True` means the decoded snapshot is byte-for-byte equal
    to the previous one (no diff computed/applied, since there is
    nothing to compute).
    `became_terminal=True` means this specific notification is the one
    that caused the ACTIVE/UNKNOWN -> TERMINAL transition (fires at
    most once per GameId - see `LiveGameRegistry.apply_notification`).
    `ignored_after_finalization=True` means the GameId was already
    TERMINAL when this notification arrived (a late/duplicate/stale
    notification); the snapshot was intentionally left untouched.
    """

    game_id: int | str
    snapshot: dict[str, Any]
    diff: LiveSnapshotDiff | None
    is_initial: bool
    duplicate: bool
    became_terminal: bool = False
    ignored_after_finalization: bool = False


@dataclass(frozen=True)
class LiveReconciliationResult:
    """
    Result of reconciling tracked GameIds against one real
    `live/headernew/en` discovery refresh (Phase 4D Step 5).

    `newly_unknown`: previously ACTIVE GameIds absent from this
        refresh (first time observed missing).
    `still_unknown`: already-UNKNOWN GameIds still absent.
    `recovered`: previously UNKNOWN GameIds present again in this
        refresh (moved back to ACTIVE).
    Finalized (TERMINAL) GameIds are never touched by reconciliation -
    discovery presence/absence carries no weight once a verified
    terminal transition has been observed.
    """

    newly_unknown: tuple[int | str, ...]
    still_unknown: tuple[int | str, ...]
    recovered: tuple[int | str, ...]


class LiveGameRegistry:
    """
    In-memory `game_id -> LiveTrackedGameState` store for a bounded,
    fixed set of tracked live GameIds.

    Mirrors `mystake.registry.game_snapshot_registry.GameSnapshotRegistry`'s
    failure-preservation contract (AGENTS.md section 5: no silent data
    loss / no partial application on failure), adapted for live
    snapshots which are plain decoded dicts (`diff_live_snapshots`
    operates on dicts, not `Snapshot` models) delivered one at a time
    by exact per-GameId MQTT PUBLISH notifications rather than fetched
    on demand.

    A notification for one GameId only ever reads/writes that GameId's
    entry - there is no shared mutable state between tracked games.

    Phase 4D adds per-GameId lifecycle tracking (`LiveLifecycleState`)
    on top of this: once a GameId is observed to reach a verified
    terminal transition, its state is frozen (TERMINAL) and further
    notifications for it are ignored rather than applied - see
    `apply_notification`/`record_failure`.
    """

    def __init__(self, tracked_game_ids: Iterable[int | str]) -> None:
        self._tracked_game_ids: tuple[int | str, ...] = tuple(
            dict.fromkeys(tracked_game_ids)
        )
        self._state: dict[int | str, LiveTrackedGameState] = {
            game_id: LiveTrackedGameState(
                game_id=game_id,
                snapshot=None,
                last_notified_at=None,
                last_error=None,
                notification_count=0,
            )
            for game_id in self._tracked_game_ids
        }

    def tracked_game_ids(self) -> tuple[int | str, ...]:
        return self._tracked_game_ids

    def active_game_ids(self) -> tuple[int | str, ...]:
        """
        Tracked GameIds not yet finalized (ACTIVE or UNKNOWN) - i.e.
        still expected to receive/process real MQTT updates.
        """
        return tuple(
            game_id
            for game_id in self._tracked_game_ids
            if not self._state[game_id].is_finalized
        )

    def finalized_game_ids(self) -> tuple[int | str, ...]:
        return tuple(
            game_id
            for game_id in self._tracked_game_ids
            if self._state[game_id].is_finalized
        )

    def get(self, game_id: int | str) -> LiveTrackedGameState | None:
        return self._state.get(game_id)

    def is_finalized(self, game_id: int | str) -> bool:
        state = self._state.get(game_id)
        return state is not None and state.is_finalized

    def apply_notification(
        self,
        game_id: int | str,
        data: dict[str, Any],
    ) -> LiveApplyOutcome | None:
        if game_id not in self._state:
            logger.warning(
                "apply_notification called for untracked game_id=%s; ignoring",
                game_id,
            )
            return None

        previous_state = self._state[game_id]

        if previous_state.is_finalized:
            logger.info(
                "game_id=%s already finalized (TERMINAL); ignoring late/stale "
                "notification, previous snapshot preserved",
                game_id,
            )
            return LiveApplyOutcome(
                game_id=game_id,
                snapshot=previous_state.snapshot,
                diff=None,
                is_initial=False,
                duplicate=False,
                became_terminal=False,
                ignored_after_finalization=True,
            )

        previous_snapshot = previous_state.snapshot
        is_initial = previous_snapshot is None

        duplicate = not is_initial and data == previous_snapshot
        diff = (
            None
            if (is_initial or duplicate)
            else diff_live_snapshots(previous_snapshot, data)
        )

        became_terminal = diff is not None and diff.match_ended
        next_lifecycle_state = (
            LiveLifecycleState.TERMINAL
            if became_terminal
            else LiveLifecycleState.ACTIVE
        )
        now = time.time()

        self._state[game_id] = replace(
            previous_state,
            snapshot=data,
            last_notified_at=now,
            last_error=None,
            notification_count=previous_state.notification_count + 1,
            lifecycle_state=next_lifecycle_state,
            finalized_at=now if became_terminal else previous_state.finalized_at,
            missing_from_discovery_since=None,
            missing_from_discovery_streak=0,
        )

        if duplicate:
            logger.info(
                "game_id=%s duplicate live notification; skipping diff", game_id
            )

        if became_terminal:
            logger.info(
                "game_id=%s verified TERMINAL transition observed: %s",
                game_id,
                [
                    f"{change.field}: {change.old!r}->{change.new!r}"
                    for change in diff.match_ended_context
                ],
            )

        return LiveApplyOutcome(
            game_id=game_id,
            snapshot=data,
            diff=diff,
            is_initial=is_initial,
            duplicate=duplicate,
            became_terminal=became_terminal,
        )

    def record_failure(self, game_id: int | str, *, error: str) -> None:
        """
        Records a transient failure (cache fetch/decode error, or a
        decoded payload that was not a dict) without touching the
        previously valid snapshot, per AGENTS.md section 5.

        A no-op for an already-finalized GameId: a failure fetching a
        stale/late notification's payload must not resurrect or alter
        a finalized game's state in any way.
        """
        if game_id not in self._state:
            logger.warning(
                "record_failure called for untracked game_id=%s; ignoring", game_id
            )
            return

        previous_state = self._state[game_id]

        if previous_state.is_finalized:
            logger.info(
                "game_id=%s already finalized (TERMINAL); ignoring failure for "
                "late/stale notification: %s",
                game_id,
                error,
            )
            return

        self._state[game_id] = replace(
            previous_state,
            last_notified_at=time.time(),
            last_error=error,
        )

        logger.warning(
            "Preserving previous live snapshot for game_id=%s after failure: %s",
            game_id,
            error,
        )

    def reconcile_discovery(
        self,
        live_game_ids: Iterable[int | str],
    ) -> LiveReconciliationResult:
        """
        Reconciles tracked non-finalized GameIds against one real
        `live/headernew/en` discovery refresh (Phase 4D Step 5).

        Absence from `live_game_ids` is never treated as terminal
        evidence (AGENTS.md: "A match disappearing from live discovery
        means it has ended" is an explicitly forbidden assumption) -
        it only moves the GameId's lifecycle state to UNKNOWN so the
        gap is visible for reporting/inspection. Reappearance, or any
        further real MQTT notification for the GameId (see
        `apply_notification`), reverts it to ACTIVE. TERMINAL GameIds
        are never touched here.
        """
        live_ids = set(live_game_ids)
        now = time.time()

        newly_unknown: list[int | str] = []
        still_unknown: list[int | str] = []
        recovered: list[int | str] = []

        for game_id in self._tracked_game_ids:
            state = self._state[game_id]

            if state.is_finalized:
                continue

            present = game_id in live_ids

            if present:
                if state.lifecycle_state is LiveLifecycleState.UNKNOWN:
                    self._state[game_id] = replace(
                        state,
                        lifecycle_state=LiveLifecycleState.ACTIVE,
                        missing_from_discovery_since=None,
                        missing_from_discovery_streak=0,
                    )
                    recovered.append(game_id)
                continue

            if state.lifecycle_state is LiveLifecycleState.UNKNOWN:
                self._state[game_id] = replace(
                    state,
                    missing_from_discovery_streak=state.missing_from_discovery_streak
                    + 1,
                )
                still_unknown.append(game_id)
            else:
                self._state[game_id] = replace(
                    state,
                    lifecycle_state=LiveLifecycleState.UNKNOWN,
                    missing_from_discovery_since=now,
                    missing_from_discovery_streak=1,
                )
                newly_unknown.append(game_id)

        return LiveReconciliationResult(
            newly_unknown=tuple(newly_unknown),
            still_unknown=tuple(still_unknown),
            recovered=tuple(recovered),
        )
