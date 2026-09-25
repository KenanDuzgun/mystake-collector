"""
Phase 5A: real prematch-to-live transition observation.

PRIMARY QUESTION (docs/handoff/handoff.md section 12): does a real
MyStake match retain its GameId when it moves from prematch
(`getheader/en`) discovery to live (`live/headernew/en`) discovery?

This is a read-only, bounded diagnostic script, not a new collector.
It reuses the existing discovery/parsing infrastructure unmodified:

- `PrematchFixtureDiscovery` (`mystake/pipeline/prematch_discovery.py`)
  for `getheader/en`.
- `LiveFixtureDiscovery` (`mystake/pipeline/live_discovery.py`) for
  `live/headernew/en`.
- `PrematchSnapshotHydrator` / `getprematchgamefull` and a single
  `live/gamenew/{GameId}` MQTT subscription, to compare market/
  selection ids once (if ever) a live counterpart is identified.

No new MQTT client, HTTP client, parser, or parallel registry is
introduced.

Matching discipline (docs/handoff/handoff.md section 12 - "do not
match fixtures authoritatively using only fuzzy team-name matching";
"if multiple candidate records match, retain ambiguity"):

- `getheader/en`'s `GameSmallItem.t1`/`t2` are raw numeric team ids
  (PROVEN this session - see the module docstring below on
  `select_candidate_fixtures`), and `live/headernew/en`'s `Games[]`
  entries expose the same raw ids via `Fixture.team1_id`/`team2_id`
  (`mystake/models/fixture.py`). This script therefore matches
  candidates by exact GameId first, then by the *exact* unordered
  team-id pair `{t1, t2}` - never by fuzzy name comparison. Any
  ambiguous match (more than one live fixture matching the same
  candidate) is reported as ambiguous, not silently resolved.

Usage:

    uv run python -u observe_prematch_to_live.py
    uv run python -u observe_prematch_to_live.py --lookahead-minutes 12 \\
        --observe-seconds 600 --poll-interval-seconds 30
"""

from __future__ import annotations

import argparse
import datetime
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from mystake.models.fixture import Fixture
from mystake.pipeline.live_discovery import LiveFixtureDiscovery
from mystake.pipeline.prematch_discovery import PrematchFixtureDiscovery
from mystake.pipeline.prematch_snapshot_hydration import PrematchSnapshotHydrator
from mystake.sources.cache.client import MystakeCacheClient
from mystake.sources.http.client import MystakeHttpClient
from mystake.sources.mqtt.client import MystakeMqttClient

logger = logging.getLogger(__name__)

DEFAULT_LOOKAHEAD_MINUTES = 15.0
DEFAULT_OBSERVE_SECONDS = 600.0
DEFAULT_POLL_INTERVAL_SECONDS = 30.0
DEFAULT_MAX_CANDIDATES = 3
DEFAULT_SPORT = "Soccer"


def parse_start_time(value: Any) -> datetime.datetime | None:
    """
    Parses a `Fixture.start_time` (`StartTime`, PROVEN ISO 8601 per
    docs/product/SCHEMA.md) into a naive `datetime`. Returns `None`
    (never a guessed time) if the value is missing or not parseable,
    so a malformed/absent StartTime excludes a fixture from candidate
    selection rather than crashing or silently defaulting it into the
    window.
    """
    if not isinstance(value, str):
        return None

    try:
        return datetime.datetime.fromisoformat(value)
    except ValueError:
        return None


def select_candidate_fixtures(
    fixtures: tuple[Fixture, ...],
    *,
    now: datetime.datetime,
    sport: str,
    lookahead_minutes: float,
    max_candidates: int,
) -> list[Fixture]:
    """
    Selects up to `max_candidates` fixtures of `sport` whose
    (parseable) `start_time` falls in `[now, now + lookahead_minutes)`,
    soonest-kickoff first. Fixtures with an unparseable/missing
    `start_time`, or a `game_id` of `None`, are excluded - never
    guessed into or out of the window.
    """
    deadline = now + datetime.timedelta(minutes=lookahead_minutes)

    candidates: list[tuple[datetime.datetime, Fixture]] = []

    for fixture in fixtures:
        if fixture.sport != sport or fixture.game_id is None:
            continue

        start_time = parse_start_time(fixture.start_time)

        if start_time is None or not (now <= start_time < deadline):
            continue

        candidates.append((start_time, fixture))

    candidates.sort(key=lambda pair: pair[0])

    return [fixture for _, fixture in candidates[:max_candidates]]


@dataclass(frozen=True)
class MatchEvidence:
    """
    Evidence found (or not found) for one prematch candidate against a
    set of live fixtures. `kind` is one of:

    - "exact_game_id": a live fixture with the identical GameId exists.
    - "team_id_pair": exactly one live fixture shares the candidate's
      unordered `{team1_id, team2_id}` pair (GameId differs or the
      live entry has no comparable id - reported as circumstantial,
      not identity-equivalent).
    - "ambiguous_team_id_pair": more than one live fixture shares the
      team-id pair; ambiguity is retained rather than picked.
    - "none": no live fixture matched by either signal.
    """

    kind: str
    live_fixtures: tuple[Fixture, ...] = field(default_factory=tuple)


def _team_id_pair(fixture: Fixture) -> frozenset | None:
    t1 = fixture.team1_id if fixture.team1_id is not None else fixture.team1
    t2 = fixture.team2_id if fixture.team2_id is not None else fixture.team2

    if t1 is None or t2 is None:
        return None

    return frozenset({t1, t2})


def match_prematch_to_live(
    candidate: Fixture,
    live_fixtures: tuple[Fixture, ...],
) -> MatchEvidence:
    exact = tuple(f for f in live_fixtures if f.game_id == candidate.game_id)

    if exact:
        return MatchEvidence(kind="exact_game_id", live_fixtures=exact)

    candidate_pair = _team_id_pair(candidate)

    if candidate_pair is None:
        return MatchEvidence(kind="none")

    by_team_pair = tuple(f for f in live_fixtures if _team_id_pair(f) == candidate_pair)

    if len(by_team_pair) == 1:
        return MatchEvidence(kind="team_id_pair", live_fixtures=by_team_pair)

    if len(by_team_pair) > 1:
        return MatchEvidence(kind="ambiguous_team_id_pair", live_fixtures=by_team_pair)

    return MatchEvidence(kind="none")


@dataclass
class CandidateObservation:
    prematch_fixture: Fixture
    still_in_prematch: bool = True
    removed_from_prematch_at: float | None = None
    live_match: MatchEvidence | None = None
    live_first_matched_at: float | None = None
    poll_log: list[str] = field(default_factory=list)


def run_observation_loop(
    candidates: list[Fixture],
    *,
    prematch_discovery: PrematchFixtureDiscovery,
    live_discovery: LiveFixtureDiscovery,
    observe_seconds: float,
    poll_interval_seconds: float,
    time_source=time.monotonic,
    sleep=time.sleep,
) -> dict[Any, CandidateObservation]:
    """
    Bounded polling loop (AGENTS.md section 4: reasonable delay between
    requests, never a tight/aggressive poll). Every
    `poll_interval_seconds`, both discovery endpoints are refreshed
    exactly once each (reusing `PrematchFixtureDiscovery.refresh()` /
    `LiveFixtureDiscovery.refresh()` - no parallel/ad hoc HTTP calls),
    and each still-unmatched candidate is checked against the latest
    live fixture list.

    A failed refresh (`refresh()` returning `None`) is logged and
    skipped for that tick - the previous registry state is preserved
    by the discovery objects themselves (AGENTS.md section 5), so this
    loop never treats a transient failure as evidence of removal.
    """
    observations = {
        c.game_id: CandidateObservation(prematch_fixture=c) for c in candidates
    }

    start = time_source()

    while time_source() - start < observe_seconds:
        tick_start = time_source()

        prematch_diff = prematch_discovery.refresh()
        live_diff = live_discovery.refresh()

        elapsed = time_source() - start

        if prematch_diff is None:
            logger.warning(
                "Prematch refresh failed at t=%.0fs; skipping this tick", elapsed
            )
        else:
            current_prematch_ids = {
                f.game_id for f in prematch_discovery.registry.list_all()
            }
            for game_id, obs in observations.items():
                if obs.still_in_prematch and game_id not in current_prematch_ids:
                    obs.still_in_prematch = False
                    obs.removed_from_prematch_at = elapsed
                    msg = f"t={elapsed:.0f}s REMOVED from prematch getheader/en"
                    obs.poll_log.append(msg)
                    logger.info("game_id=%s %s", game_id, msg)

        if live_diff is None:
            logger.warning(
                "Live refresh failed at t=%.0fs; skipping this tick", elapsed
            )
        else:
            live_fixtures = live_discovery.registry.list_all()
            for game_id, obs in observations.items():
                if (
                    obs.live_match is not None
                    and obs.live_match.kind == "exact_game_id"
                ):
                    continue

                evidence = match_prematch_to_live(obs.prematch_fixture, live_fixtures)

                if evidence.kind != "none":
                    if obs.live_match is None or obs.live_match.kind == "none":
                        obs.live_first_matched_at = elapsed

                    obs.live_match = evidence
                    live_ids = [f.game_id for f in evidence.live_fixtures]
                    msg = (
                        f"t={elapsed:.0f}s live match kind={evidence.kind} "
                        f"live_game_ids={live_ids}"
                    )
                    obs.poll_log.append(msg)
                    logger.info("game_id=%s %s", game_id, msg)

        remaining_budget = observe_seconds - (time_source() - start)
        tick_elapsed = time_source() - tick_start
        sleep_for = poll_interval_seconds - tick_elapsed

        if sleep_for > 0 and remaining_budget > 0:
            sleep(min(sleep_for, remaining_budget))

    return observations


def deepen_market_selection_comparison(
    observation: CandidateObservation,
    *,
    http_client: MystakeHttpClient,
    mqtt_client: MystakeMqttClient,
    timeout_seconds: float = 20.0,
) -> str:
    """
    Best-effort, single-shot comparison (items 7/8 of the Phase 5A
    task): fetches one authoritative `getprematchgamefull` snapshot for
    the prematch candidate and, if a live counterpart was identified,
    subscribes to its exact `live/gamenew/{GameId}` topic and waits
    (bounded by `timeout_seconds`) for exactly one PUBLISH, then
    compares market-id and selection-id sets between the two snapshots.

    Never called from `run_observation_loop` - only invoked once, after
    the loop, for candidates that actually matched, keeping request
    volume bounded (AGENTS.md section 4).
    """
    import threading

    from mystake.pipeline.notification_processor import NotificationProcessor
    from mystake.sources.cache.client import MystakeCacheClient
    from mystake.sources.mqtt.client import ListenerShutdown

    prematch_game_id = observation.prematch_fixture.game_id
    hydrator = PrematchSnapshotHydrator(http_client)
    prematch_snapshot = hydrator.fetch(prematch_game_id)

    if prematch_snapshot is None:
        return (
            f"game_id={prematch_game_id}: prematch snapshot fetch failed "
            "(fixture may have already kicked off / been delisted) - "
            "market/selection stability NOT VERIFIED"
        )

    prematch_market_ids = {m.id for m in prematch_snapshot.markets}
    prematch_selection_ids = {
        s.id for m in prematch_snapshot.markets for s in m.selections
    }

    if observation.live_match is None or not observation.live_match.live_fixtures:
        return (
            f"game_id={prematch_game_id}: no live counterpart identified - "
            f"prematch market_ids={len(prematch_market_ids)} "
            f"selection_ids={len(prematch_selection_ids)}; "
            "market/selection stability NOT VERIFIED (no live snapshot to compare)"
        )

    live_fixture = observation.live_match.live_fixtures[0]
    live_game_id = live_fixture.game_id
    topic = f"live/gamenew/{live_game_id}"

    cache_client = MystakeCacheClient()
    notification_processor = NotificationProcessor(cache_client=cache_client)

    live_data: dict[str, Any] | None = None
    timeout_timer = threading.Timer(timeout_seconds, mqtt_client.request_shutdown)
    timeout_timer.daemon = True

    try:
        mqtt_client.subscribe(topic)
        logger.info("Subscribed topic=%s for one-shot live snapshot", topic)

        timeout_timer.start()

        try:
            while True:
                message = mqtt_client.receive_publish()

                if message.topic != topic:
                    continue

                result = notification_processor.process(message)

                if isinstance(result.data, dict):
                    live_data = result.data
                    break
        except ListenerShutdown:
            logger.info(
                "No live/gamenew PUBLISH for topic=%s within %.0fs",
                topic,
                timeout_seconds,
            )
        finally:
            timeout_timer.cancel()

            try:
                mqtt_client.unsubscribe(topic)
            except Exception:
                logger.exception("Failed to unsubscribe one-shot topic=%s", topic)
    finally:
        cache_client.close()

    if live_data is None:
        return (
            f"game_id={prematch_game_id}->live_game_id={live_game_id}: "
            f"no live/gamenew PUBLISH received within {timeout_seconds:.0f}s - "
            "market/selection stability NOT VERIFIED"
        )

    gmk = live_data.get("gmk")
    live_market_ids = (
        {item.get("mid") for item in gmk if isinstance(item, dict)}
        if isinstance(gmk, list)
        else set()
    )
    live_selection_ids = (
        {item.get("sid") for item in gmk if isinstance(item, dict)}
        if isinstance(gmk, list)
        else set()
    )

    market_overlap = prematch_market_ids & live_market_ids
    selection_overlap = prematch_selection_ids & live_selection_ids

    return (
        f"game_id={prematch_game_id}->live_game_id={live_game_id}: "
        f"prematch market_ids={len(prematch_market_ids)} "
        f"live market_ids={len(live_market_ids)} "
        f"overlap={len(market_overlap)}; "
        f"prematch selection_ids={len(prematch_selection_ids)} "
        f"live selection_ids={len(live_selection_ids)} "
        f"overlap={len(selection_overlap)}"
    )


def print_final_report(observations: dict[Any, CandidateObservation]) -> None:
    print()
    print("=" * 80)
    print("PHASE 5A FINAL REPORT (observe_prematch_to_live.py)")
    print("=" * 80)

    for game_id, obs in observations.items():
        fixture = obs.prematch_fixture
        print()
        print(f"Candidate prematch GameId={game_id}")
        print(
            f"  Sport={fixture.sport} Champ={fixture.champ} "
            f"StartTime={fixture.start_time} team1_id={fixture.raw.get('t1')} "
            f"team2_id={fixture.raw.get('t2')}"
        )
        print(
            f"  Still in prematch getheader/en at end of window: {obs.still_in_prematch}"
        )
        if obs.removed_from_prematch_at is not None:
            print(f"  Removed from prematch at t={obs.removed_from_prematch_at:.0f}s")

        if obs.live_match is None or obs.live_match.kind == "none":
            print("  Live counterpart: NOT VERIFIED (no match observed in window)")
        else:
            live_ids = [f.game_id for f in obs.live_match.live_fixtures]
            print(
                f"  Live counterpart: kind={obs.live_match.kind} "
                f"live_game_ids={live_ids} "
                f"first_matched_at=t={obs.live_first_matched_at:.0f}s"
            )
            if obs.live_match.kind == "exact_game_id":
                print("  IDENTITY PRESERVED: same GameId used in prematch and live.")
            elif obs.live_match.kind == "team_id_pair":
                print(
                    "  IDENTITY NOT PRESERVED: live GameId differs from prematch "
                    "GameId; matched only via exact team-id pair."
                )
            elif obs.live_match.kind == "ambiguous_team_id_pair":
                print(
                    "  AMBIGUOUS: multiple live fixtures share this team-id pair; "
                    "not treated as an authoritative match."
                )

        for entry in obs.poll_log:
            print(f"    - {entry}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--sport",
        default=DEFAULT_SPORT,
        help="sport name to select candidates from (must match Fixture.sport exactly)",
    )
    parser.add_argument(
        "--lookahead-minutes",
        type=float,
        default=DEFAULT_LOOKAHEAD_MINUTES,
        help="only consider fixtures whose StartTime falls within this many minutes from now",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=DEFAULT_MAX_CANDIDATES,
        help="max number of candidate fixtures to observe (1-3 per Phase 5A scope)",
    )
    parser.add_argument(
        "--observe-seconds",
        type=float,
        default=DEFAULT_OBSERVE_SECONDS,
        help="bounded total observation window in seconds",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help="delay between discovery refresh ticks (AGENTS.md section 4: no aggressive polling)",
    )
    parser.add_argument(
        "--deepen",
        action="store_true",
        help=(
            "after the observation window, for each matched candidate, fetch one "
            "getprematchgamefull snapshot and one live/gamenew snapshot and compare "
            "market/selection id sets (bounded, single-shot per candidate)"
        ),
    )

    return parser


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    parser = build_arg_parser()
    args = parser.parse_args()

    if args.max_candidates < 1 or args.max_candidates > 3:
        parser.error("--max-candidates must be between 1 and 3 (Phase 5A scope)")

    http_client = MystakeHttpClient()
    cache_client = MystakeCacheClient()

    prematch_discovery = PrematchFixtureDiscovery(http_client=http_client)
    live_discovery = LiveFixtureDiscovery(cache_client=cache_client)

    observations: dict[Any, CandidateObservation] = {}

    try:
        print("Running initial real getheader/en prematch discovery...")
        prematch_diff = prematch_discovery.refresh()

        if prematch_diff is None:
            print("Prematch discovery FAILED. Aborting.")
            return

        all_fixtures = prematch_discovery.registry.list_all()
        print(f"Total prematch fixture count: {len(all_fixtures)}")

        now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None, microsecond=0)
        candidates = select_candidate_fixtures(
            all_fixtures,
            now=now,
            sport=args.sport,
            lookahead_minutes=args.lookahead_minutes,
            max_candidates=args.max_candidates,
        )

        if not candidates:
            print(
                f"No real {args.sport} fixtures found starting within "
                f"{args.lookahead_minutes:.0f} minutes of {now.isoformat()}. "
                "NOT VERIFIED - nothing to observe."
            )
            return

        print(f"Selected {len(candidates)} candidate(s) (soonest kickoff first):")
        for fixture in candidates:
            print(
                f"  GameId={fixture.game_id} StartTime={fixture.start_time} "
                f"Champ={fixture.champ} t1={fixture.raw.get('t1')} "
                f"t2={fixture.raw.get('t2')}"
            )

        print("Running initial real live/headernew/en discovery...")
        live_diff = live_discovery.refresh()
        if live_diff is None:
            print(
                "Initial live discovery FAILED (continuing - polling loop will retry)."
            )
        else:
            print(
                f"Total live fixture count: {len(live_discovery.registry.list_all())}"
            )

        print(
            f"Observing for up to {args.observe_seconds:.0f}s "
            f"(poll every {args.poll_interval_seconds:.0f}s)..."
        )

        observations = run_observation_loop(
            candidates,
            prematch_discovery=prematch_discovery,
            live_discovery=live_discovery,
            observe_seconds=args.observe_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
        )

        if args.deepen:
            matched = [
                obs
                for obs in observations.values()
                if obs.live_match is not None and obs.live_match.live_fixtures
            ]

            if not matched:
                print("No matched candidates to deepen (--deepen requested).")
            else:
                mqtt_client = MystakeMqttClient()
                try:
                    mqtt_client.connect_with_retry()
                    for obs in matched:
                        summary = deepen_market_selection_comparison(
                            obs,
                            http_client=http_client,
                            mqtt_client=mqtt_client,
                        )
                        print(f"DEEPEN: {summary}")
                finally:
                    mqtt_client.close()

    except KeyboardInterrupt:
        logger.info("Interrupted by user")

    except Exception:
        logger.exception(
            "observe_prematch_to_live.py stopping due to an unhandled error"
        )
        raise

    finally:
        http_client.close()
        cache_client.close()

    print_final_report(observations)


if __name__ == "__main__":
    main()
