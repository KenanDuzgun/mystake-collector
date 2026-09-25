"""
Phase 5B: automatic prematch-to-live tracking handoff, end to end.

Real-network findings this executable relies on (Phase 5A,
`observe_prematch_to_live.py`, docs/handoff/handoff.md section 12):
a MyStake fixture keeps the exact same GameId when it moves from
prematch (`getheader/en`) discovery to live (`live/headernew/en`)
discovery, and the two discovery views can overlap for tens of seconds
before the GameId disappears from prematch discovery.

This script wires the existing prematch and live tracking stacks
together via `PrematchToLiveHandoffCoordinator`
(`mystake/pipeline/prematch_to_live_handoff.py`) - no new transport,
parser, or parallel registry is introduced; this file is orchestration
only:

1. Real `getheader/en` prematch discovery, bounded selection of
   upcoming fixtures (reuses `select_candidate_fixtures` from
   `observe_prematch_to_live.py`).
2. `GameSnapshotRegistry` + `PrematchOddsTracker` prematch tracking for
   those candidates (same components as `watch_prematch_odds.py`).
3. An initially-empty `LiveGameRegistry` + `LiveOddsDispatcher` (same
   components as `watch_live_odds.py`) that GameIds join dynamically
   the moment they are observed in live discovery.
4. A background timer thread that periodically calls
   `PrematchToLiveHandoffCoordinator.tick()` - bounded discovery
   refresh + revalidation, never a tight/uncontrolled loop - while the
   main thread runs the ordinary blocking MQTT receive loop
   (`MystakeMqttClient.receive_publish` -> `LiveOddsDispatcher.handle`,
   exactly as in `watch_live_odds.py`). `MystakeMqttClient` guards its
   physical socket reads/writes with a lock (see
   `mystake/sources/mqtt/client.py`) precisely so the coordinator can
   safely call `subscribe()` on that background thread the moment it
   detects a live transition, without waiting for the main loop's
   current blocking receive to return.

Usage:

    uv run python -u watch_prematch_to_live.py
    uv run python -u watch_prematch_to_live.py --sport Soccer \\
        --lookahead-minutes 20 --max-candidates 3 --observe-seconds 900 \\
        --tick-interval-seconds 20
"""

from __future__ import annotations

import argparse
import datetime
import logging
import signal
import threading

from mystake.pipeline.live_discovery import LiveFixtureDiscovery
from mystake.pipeline.live_odds_dispatcher import LiveOddsDispatcher
from mystake.pipeline.notification_processor import NotificationProcessor
from mystake.pipeline.prematch_discovery import PrematchFixtureDiscovery
from mystake.pipeline.prematch_odds_tracker import PrematchOddsTracker
from mystake.pipeline.prematch_to_live_handoff import (
    HandoffPhase,
    PrematchToLiveHandoffCoordinator,
)
from mystake.registry.game_snapshot_registry import GameSnapshotRegistry
from mystake.registry.live_game_registry import LiveGameRegistry, LiveLifecycleState
from mystake.sources.cache.client import MystakeCacheClient
from mystake.sources.http.client import MystakeHttpClient
from mystake.sources.mqtt.client import ListenerShutdown, MystakeMqttClient
from observe_prematch_to_live import select_candidate_fixtures
from watch_live_odds import log_outcome as log_live_outcome
from watch_prematch_odds import log_outcome as log_prematch_outcome

logger = logging.getLogger(__name__)

DEFAULT_SPORT = "Soccer"
DEFAULT_LOOKAHEAD_MINUTES = 15.0
DEFAULT_MAX_CANDIDATES = 3
DEFAULT_OBSERVE_SECONDS = 900.0
DEFAULT_TICK_INTERVAL_SECONDS = 20.0


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
        help="max number of candidate fixtures to track (bounded, AGENTS.md section 4)",
    )
    parser.add_argument(
        "--observe-seconds",
        type=float,
        default=DEFAULT_OBSERVE_SECONDS,
        help="bounded total observation window in seconds",
    )
    parser.add_argument(
        "--tick-interval-seconds",
        type=float,
        default=DEFAULT_TICK_INTERVAL_SECONDS,
        help="delay between handoff coordinator ticks (discovery refresh + revalidation)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="log full raw snapshot payloads (never enable with untrusted output sinks)",
    )

    return parser


def schedule_ticks(
    coordinator: PrematchToLiveHandoffCoordinator,
    mqtt_client: MystakeMqttClient,
    *,
    interval_seconds: float,
    debug: bool,
) -> threading.Timer | None:
    """
    Self-rescheduling wrapper around `coordinator.tick()`, mirroring
    `watch_live_odds.schedule_reconciliation`. Bounded by listener
    shutdown so this background work never outlives the observation
    window.
    """
    if interval_seconds <= 0 or mqtt_client.shutdown_requested:
        return None

    def _tick() -> None:
        if mqtt_client.shutdown_requested:
            return

        result = coordinator.tick()

        for game_id in result.newly_detected:
            logger.info(
                "game_id=%s LIVE DISCOVERY DETECTED (same GameId as prematch "
                "candidate)",
                game_id,
            )

        for game_id in result.handoff_failed:
            logger.warning(
                "game_id=%s live handoff attempt failed this tick; retryable",
                game_id,
            )

        for game_id in result.handed_off:
            logger.info(
                "game_id=%s AUTOMATIC HANDOFF complete (subscribed, independent "
                "live state initialized)",
                game_id,
            )

        for game_id in result.cleaned_up:
            logger.info(
                "game_id=%s PREMATCH CLEANUP complete (removed from getheader/en; "
                "live tracking continues independently)",
                game_id,
            )

        for game_id, outcome in result.prematch_outcomes.items():
            log_prematch_outcome(game_id, outcome, debug=debug)

        schedule_ticks(
            coordinator,
            mqtt_client,
            interval_seconds=interval_seconds,
            debug=debug,
        )

    timer = threading.Timer(interval_seconds, _tick)
    timer.daemon = True
    timer.start()
    return timer


def install_shutdown_signal_handlers(mqtt_client: MystakeMqttClient) -> None:
    def handle_shutdown_signal(signum: int, frame: object) -> None:
        logger.info(
            "Received signal=%s; requesting listener shutdown",
            signal.Signals(signum).name,
        )
        mqtt_client.request_shutdown()

    signal.signal(signal.SIGINT, handle_shutdown_signal)
    signal.signal(signal.SIGTERM, handle_shutdown_signal)


def run(
    mqtt_client: MystakeMqttClient,
    dispatcher: LiveOddsDispatcher,
    coordinator: PrematchToLiveHandoffCoordinator,
    stats: dict[int | str, dict[str, int]],
    *,
    observe_seconds: float,
    tick_interval_seconds: float,
    debug: bool,
) -> None:
    mqtt_client.connect_with_retry()
    logger.info("MQTT connection established")

    shutdown_timer = threading.Timer(observe_seconds, mqtt_client.request_shutdown)
    shutdown_timer.daemon = True
    shutdown_timer.start()

    tick_timer = schedule_ticks(
        coordinator,
        mqtt_client,
        interval_seconds=tick_interval_seconds,
        debug=debug,
    )

    try:
        logger.info(
            "Listening for live notifications (dynamic subscriptions as GameIds "
            "hand off) for up to %.0fs",
            observe_seconds,
        )

        while True:
            message = mqtt_client.receive_publish()

            outcome = dispatcher.handle(message)

            if outcome is not None:
                stats.setdefault(
                    outcome.game_id,
                    {
                        "notification_count": 0,
                        "price_changes": 0,
                        "score_changes": 0,
                        "selection_changes": 0,
                    },
                )
                log_live_outcome(outcome.game_id, outcome, stats, debug=debug)

    finally:
        shutdown_timer.cancel()
        if tick_timer is not None:
            tick_timer.cancel()


def serve(
    mqtt_client: MystakeMqttClient,
    dispatcher: LiveOddsDispatcher,
    coordinator: PrematchToLiveHandoffCoordinator,
    stats: dict[int | str, dict[str, int]],
    http_client: MystakeHttpClient,
    cache_client: MystakeCacheClient,
    *,
    observe_seconds: float,
    tick_interval_seconds: float,
    debug: bool,
) -> None:
    try:
        run(
            mqtt_client,
            dispatcher,
            coordinator,
            stats,
            observe_seconds=observe_seconds,
            tick_interval_seconds=tick_interval_seconds,
            debug=debug,
        )

    except (KeyboardInterrupt, ListenerShutdown) as exc:
        logger.info("Listener stopping (%s)", type(exc).__name__)

    except Exception:
        logger.exception("Listener stopping due to an unhandled error")
        raise

    finally:
        mqtt_client.close()
        http_client.close()
        cache_client.close()
        logger.info("Listener stopped; resources closed")


def print_final_report(
    coordinator: PrematchToLiveHandoffCoordinator,
    stats: dict[int | str, dict[str, int]],
    live_registry: LiveGameRegistry,
) -> None:
    print()
    print("=" * 80)
    print("PHASE 5B FINAL REPORT (watch_prematch_to_live.py)")
    print("=" * 80)

    for game_id, record in coordinator.records().items():
        print()
        print(f"GameId={game_id}")
        print(f"  Final phase             : {record.phase}")
        print(f"  Live detected at        : {record.live_detected_at}")
        print(f"  Live handed off at      : {record.live_handed_off_at}")
        print(f"  Prematch removed at     : {record.prematch_removed_at}")
        print(f"  Prematch cleaned up at  : {record.prematch_cleaned_up_at}")
        print(f"  Handoff attempts        : {record.handoff_attempts}")
        print(f"  Last error              : {record.last_error}")

        if record.phase is HandoffPhase.PREMATCH_TRACKING:
            print("  AUTOMATIC HANDOFF: NOT OBSERVED for this GameId")
            continue

        live_state = live_registry.get(game_id)
        game_stats = stats.get(game_id, {})

        print(f"  Notifications received  : {game_stats.get('notification_count', 0)}")
        print(f"  Price changes           : {game_stats.get('price_changes', 0)}")
        print(f"  Score changes           : {game_stats.get('score_changes', 0)}")

        if live_state is not None:
            print(f"  Live lifecycle state    : {live_state.lifecycle_state}")
            print(
                "  Initial live snapshot   : "
                f"{'received' if live_state.snapshot is not None else 'NOT received'}"
            )
            if live_state.lifecycle_state is LiveLifecycleState.TERMINAL:
                print(f"  Finalized at            : {live_state.finalized_at}")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    parser = build_arg_parser()
    args = parser.parse_args()

    if args.max_candidates < 1:
        parser.error("--max-candidates must be at least 1")

    http_client = MystakeHttpClient()
    cache_client = MystakeCacheClient()
    mqtt_client = MystakeMqttClient()

    install_shutdown_signal_handlers(mqtt_client)

    prematch_discovery = PrematchFixtureDiscovery(http_client=http_client)
    live_discovery = LiveFixtureDiscovery(cache_client=cache_client)

    print("Running initial real getheader/en prematch discovery...")
    prematch_diff = prematch_discovery.refresh()

    if prematch_diff is None:
        print("Prematch discovery FAILED. Aborting.")
        http_client.close()
        cache_client.close()
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
            "Nothing to track."
        )
        http_client.close()
        cache_client.close()
        return

    candidate_game_ids = [fixture.game_id for fixture in candidates]

    print(f"Selected {len(candidates)} candidate(s) (soonest kickoff first):")
    for fixture in candidates:
        print(
            f"  GameId={fixture.game_id} StartTime={fixture.start_time} "
            f"Champ={fixture.champ} {fixture.team1} vs {fixture.team2}"
        )

    prematch_registry = GameSnapshotRegistry(tracked_game_ids=candidate_game_ids)
    prematch_tracker = PrematchOddsTracker(http_client, prematch_registry)

    print("Hydrating initial real prematch snapshots...")
    for game_id, outcome in prematch_tracker.hydrate_all_tracked().items():
        log_prematch_outcome(game_id, outcome, debug=args.debug)

    live_registry = LiveGameRegistry(tracked_game_ids=())
    notification_processor = NotificationProcessor(cache_client=cache_client)
    dispatcher = LiveOddsDispatcher(
        registry=live_registry,
        notification_processor=notification_processor,
        mqtt_client=mqtt_client,
    )

    coordinator = PrematchToLiveHandoffCoordinator(
        prematch_discovery=prematch_discovery,
        live_discovery=live_discovery,
        prematch_tracker=prematch_tracker,
        live_registry=live_registry,
        mqtt_client=mqtt_client,
        candidate_game_ids=candidate_game_ids,
    )

    stats: dict[int | str, dict[str, int]] = {}

    print(
        f"Observing for up to {args.observe_seconds:.0f}s "
        f"(coordinator tick every {args.tick_interval_seconds:.0f}s)..."
    )

    serve(
        mqtt_client,
        dispatcher,
        coordinator,
        stats,
        http_client,
        cache_client,
        observe_seconds=args.observe_seconds,
        tick_interval_seconds=args.tick_interval_seconds,
        debug=args.debug,
    )

    print_final_report(coordinator, stats, live_registry)


if __name__ == "__main__":
    main()
