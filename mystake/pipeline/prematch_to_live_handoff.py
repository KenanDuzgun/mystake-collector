"""
Phase 5B: automatic prematch-to-live tracking handoff.

Phase 5A (`observe_prematch_to_live.py`) proved, against two real
fixtures (GameId=76553416, GameId=76553419), that a MyStake match keeps
the exact same GameId when it moves from prematch (`getheader/en`)
discovery to live (`live/headernew/en`) discovery, and that the two
discovery views can overlap for a real, observed ~29 seconds before the
GameId disappears from prematch discovery.

This module adds the minimal coordination needed to turn that observed
identity behavior into an automatic tracking handoff, entirely by
composing existing, unmodified infrastructure:

- `PrematchFixtureDiscovery` / `PrematchOddsTracker` /
  `GameSnapshotRegistry` for prematch tracking (unchanged).
- `LiveFixtureDiscovery` / `LiveGameRegistry` / `LiveOddsDispatcher`
  for live tracking (unchanged, aside from `LiveGameRegistry.add_game`
  - see its docstring - which lets a *new* GameId join an
  already-running registry instead of only being fixed at
  construction).
- The existing `MystakeMqttClient.subscribe`, which already blocks for
  a real SUBACK or raises.

No new transport, parser, or parallel registry is introduced.

State machine (per candidate GameId), matching the task's diagram:

    PREMATCH_TRACKING
        -> (same GameId observed in live/headernew/en discovery)
    LIVE_HANDED_OFF
        -> (GameId subsequently absent from getheader/en discovery)
    PREMATCH_CLEANED_UP

Detection discipline: exact GameId equality only (never fuzzy
team-name matching, never kickoff-time-based auto-handoff - the task
is explicit that the live discovery observation itself is the trigger).

Failure/idempotency discipline:

- A GameId only ever leaves `PREMATCH_TRACKING` once a live
  subscription's SUBACK has actually been confirmed; a failed
  subscribe attempt leaves the GameId in `PREMATCH_TRACKING`, so the
  next `tick()` retries automatically - prematch tracking for it is
  entirely unaffected by a live-handoff failure.
- A GameId already present in the live registry (a previous successful
  handoff, or - defensively - some other reason) is treated as already
  handed off rather than re-subscribed.
- Once a GameId reaches `PREMATCH_CLEANED_UP` it is permanently
  excluded from further prematch hydration; nothing in this module
  ever moves a GameId backwards out of `PREMATCH_CLEANED_UP` or
  `LIVE_HANDED_OFF`.
- Prematch disappearance alone never finalizes/cleans up a GameId that
  has not already been successfully handed off to live tracking - that
  would discard the only tracking state that fixture has. Live
  match-end finalization remains entirely owned by the existing Phase
  4D `LiveGameRegistry`/`LiveOddsDispatcher` lifecycle.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import StrEnum

from mystake.pipeline.live_discovery import LiveFixtureDiscovery
from mystake.pipeline.live_odds_dispatcher import topic_for_game_id
from mystake.pipeline.prematch_discovery import PrematchFixtureDiscovery
from mystake.pipeline.prematch_odds_tracker import PrematchOddsTracker
from mystake.registry.game_snapshot_registry import FetchOutcome
from mystake.registry.live_game_registry import LiveGameRegistry
from mystake.sources.mqtt.client import MystakeMqttClient

logger = logging.getLogger(__name__)


class HandoffPhase(StrEnum):
    PREMATCH_TRACKING = "PREMATCH_TRACKING"
    LIVE_HANDED_OFF = "LIVE_HANDED_OFF"
    PREMATCH_CLEANED_UP = "PREMATCH_CLEANED_UP"


@dataclass(frozen=True)
class HandoffRecord:
    game_id: int | str
    phase: HandoffPhase = HandoffPhase.PREMATCH_TRACKING
    live_detected_at: float | None = None
    live_handed_off_at: float | None = None
    prematch_removed_at: float | None = None
    prematch_cleaned_up_at: float | None = None
    last_error: str | None = None
    handoff_attempts: int = 0


@dataclass(frozen=True)
class HandoffTickResult:
    prematch_refresh_ok: bool
    live_refresh_ok: bool
    newly_detected: tuple[int | str, ...]
    handed_off: tuple[int | str, ...]
    handoff_failed: tuple[int | str, ...]
    cleaned_up: tuple[int | str, ...]
    prematch_outcomes: dict[int | str, FetchOutcome | None]


class PrematchToLiveHandoffCoordinator:
    """
    Bounded orchestrator for a fixed, small candidate set of GameIds
    (AGENTS.md section 4: no unbounded fan-out). One `tick()` call:

    1. Refreshes prematch discovery and revalidates prematch odds for
       every candidate not yet cleaned up (reuses
       `PrematchOddsTracker.hydrate_many` - bounded concurrency/pacing
       unchanged).
    2. Refreshes live discovery and, for every candidate still in
       `PREMATCH_TRACKING`, checks exact GameId membership in the
       latest live fixture set - the transition trigger.
    3. Attempts (idempotent, retryable) live handoff for every
       newly-observed-live candidate.
    4. Cleans up prematch tracking for any already-handed-off
       candidate that has since disappeared from prematch discovery.

    A failed discovery refresh (`refresh()` returning `None`) leaves
    the previous registry state untouched (per the underlying
    discovery classes' own contract) and this coordinator skips the
    dependent step for that tick rather than treating the failure as
    evidence of anything.
    """

    def __init__(
        self,
        *,
        prematch_discovery: PrematchFixtureDiscovery,
        live_discovery: LiveFixtureDiscovery,
        prematch_tracker: PrematchOddsTracker,
        live_registry: LiveGameRegistry,
        mqtt_client: MystakeMqttClient,
        candidate_game_ids: Iterable[int | str],
        time_source=time.monotonic,
    ) -> None:
        self.prematch_discovery = prematch_discovery
        self.live_discovery = live_discovery
        self.prematch_tracker = prematch_tracker
        self.live_registry = live_registry
        self.mqtt_client = mqtt_client
        self._time_source = time_source

        self._records: dict[int | str, HandoffRecord] = {
            game_id: HandoffRecord(game_id=game_id)
            for game_id in dict.fromkeys(candidate_game_ids)
        }

    def records(self) -> dict[int | str, HandoffRecord]:
        return dict(self._records)

    def _active_prematch_ids(self) -> tuple[int | str, ...]:
        return tuple(
            game_id
            for game_id, record in self._records.items()
            if record.phase is not HandoffPhase.PREMATCH_CLEANED_UP
        )

    def _not_yet_handed_off_ids(self) -> tuple[int | str, ...]:
        return tuple(
            game_id
            for game_id, record in self._records.items()
            if record.phase is HandoffPhase.PREMATCH_TRACKING
        )

    def tick(self) -> HandoffTickResult:
        now = self._time_source()

        prematch_diff = self.prematch_discovery.refresh()
        prematch_refresh_ok = prematch_diff is not None
        prematch_outcomes: dict[int | str, FetchOutcome | None] = {}

        if prematch_refresh_ok:
            prematch_outcomes = self._revalidate_active_prematch()

        live_diff = self.live_discovery.refresh()
        live_refresh_ok = live_diff is not None

        newly_detected: list[int | str] = []
        handed_off: list[int | str] = []
        handoff_failed: list[int | str] = []

        if live_refresh_ok:
            newly_detected, handed_off, handoff_failed = self._process_live_transitions(
                now
            )

        cleaned_up: list[int | str] = []

        if prematch_refresh_ok:
            cleaned_up = self._cleanup_removed_from_prematch(now)

        return HandoffTickResult(
            prematch_refresh_ok=prematch_refresh_ok,
            live_refresh_ok=live_refresh_ok,
            newly_detected=tuple(newly_detected),
            handed_off=tuple(handed_off),
            handoff_failed=tuple(handoff_failed),
            cleaned_up=tuple(cleaned_up),
            prematch_outcomes=prematch_outcomes,
        )

    def _revalidate_active_prematch(self) -> dict[int | str, FetchOutcome | None]:
        active_ids = self._active_prematch_ids()

        if not active_ids:
            return {}

        outcomes = self.prematch_tracker.hydrate_many(active_ids)

        for game_id, outcome in outcomes.items():
            if outcome is not None and outcome.failed:
                logger.warning(
                    "game_id=%s prematch hydration failed during handoff "
                    "tracking; previous snapshot preserved",
                    game_id,
                )

        return outcomes

    def _process_live_transitions(
        self, now: float
    ) -> tuple[list[int | str], list[int | str], list[int | str]]:
        live_game_ids = {
            fixture.game_id for fixture in self.live_discovery.registry.list_all()
        }

        newly_detected: list[int | str] = []
        handed_off: list[int | str] = []
        handoff_failed: list[int | str] = []

        for game_id in self._not_yet_handed_off_ids():
            if self.mqtt_client.shutdown_requested:
                logger.info(
                    "Shutdown requested; not attempting further live handoffs "
                    "this tick (game_id=%s deferred to next tick)",
                    game_id,
                )
                break

            if game_id not in live_game_ids:
                continue

            record = self._records[game_id]

            if record.live_detected_at is None:
                self._records[game_id] = replace(record, live_detected_at=now)
                newly_detected.append(game_id)

            if self._attempt_handoff(game_id, now=now):
                handed_off.append(game_id)
            else:
                handoff_failed.append(game_id)

        return newly_detected, handed_off, handoff_failed

    def _attempt_handoff(self, game_id: int | str, *, now: float) -> bool:
        """
        Idempotent, retryable single-GameId handoff attempt:

        1. If `game_id` is already tracked by `live_registry` (a prior
           successful attempt), just mark this record handed off -
           never issues a duplicate subscription.
        2. Otherwise subscribes to the exact `live/gamenew/{GameId}`
           topic (blocks for a real SUBACK or raises).
        3. Registers the GameId in the live registry with fresh,
           independent state - prematch market/selection state is
           never copied in; the actual initial live snapshot arrives
           through the normal MQTT PUBLISH -> `LiveOddsDispatcher`
           path, exactly as for any other tracked live GameId.

        Returns `True` on success (including "already handed off"),
        `False` on a failure left for the next `tick()` to retry.
        """
        record = self._records[game_id]

        if game_id in self.live_registry.tracked_game_ids():
            self._records[game_id] = replace(
                record,
                phase=HandoffPhase.LIVE_HANDED_OFF,
                live_handed_off_at=record.live_handed_off_at or now,
            )
            return True

        self._records[game_id] = replace(
            record, handoff_attempts=record.handoff_attempts + 1
        )

        topic = topic_for_game_id(game_id)

        try:
            self.mqtt_client.subscribe(topic)
        except Exception as exc:
            error = str(exc)
            logger.exception(
                "game_id=%s live handoff subscribe failed (topic=%s); "
                "will retry on next tick, prematch tracking unaffected",
                game_id,
                topic,
            )
            self._records[game_id] = replace(self._records[game_id], last_error=error)
            return False

        self.live_registry.add_game(game_id)

        self._records[game_id] = replace(
            self._records[game_id],
            phase=HandoffPhase.LIVE_HANDED_OFF,
            live_handed_off_at=now,
            last_error=None,
        )

        logger.info(
            "game_id=%s AUTOMATIC HANDOFF: subscribed topic=%s (SUBACK confirmed); "
            "independent live snapshot state initialized",
            game_id,
            topic,
        )

        return True

    def _cleanup_removed_from_prematch(self, now: float) -> list[int | str]:
        prematch_game_ids = {
            fixture.game_id for fixture in self.prematch_discovery.registry.list_all()
        }

        cleaned_up: list[int | str] = []

        for game_id, record in list(self._records.items()):
            if record.phase is not HandoffPhase.LIVE_HANDED_OFF:
                continue

            if game_id in prematch_game_ids:
                continue

            self._records[game_id] = replace(
                record,
                phase=HandoffPhase.PREMATCH_CLEANED_UP,
                prematch_removed_at=record.prematch_removed_at or now,
                prematch_cleaned_up_at=now,
            )
            cleaned_up.append(game_id)

            logger.info(
                "game_id=%s prematch cleanup complete (live tracking continues "
                "independently)",
                game_id,
            )

        return cleaned_up
