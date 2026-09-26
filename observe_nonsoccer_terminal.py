"""
Phase 6E: bounded real-network observation of non-Soccer terminal
lifecycle behavior (Basketball, Tennis prioritized).

Reuses existing infrastructure end-to-end - live discovery
(`LiveFixtureDiscovery`), the MQTT client
(`mystake.sources.mqtt.client.MystakeMqttClient`), the live game
registry/dispatcher (`LiveGameRegistry`/`LiveOddsDispatcher`), and
`watch_live_odds.py`'s reconciliation/reporting helpers - identical to
`observe_live_soak.py`'s orchestration, with only fixture selection
changed to prioritize non-Soccer sports instead of one-per-sport
diversity.

No sport-specific terminal-state logic is added here. This script is
observation-only: it records what `LiveGameRegistry`/`diff_live_snapshots`
(currently sport-agnostic - see `_match_meets_ended_criteria`) actually
does when applied to real Basketball/Tennis notifications.

Usage:

    uv run python -u observe_nonsoccer_terminal.py --observe-seconds 1500
"""

from __future__ import annotations

import argparse
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from mystake.config import LIVE_TRACKED_GAMES_MAX
from mystake.models.fixture import Fixture
from mystake.pipeline.live_discovery import LiveFixtureDiscovery
from mystake.pipeline.live_odds_dispatcher import LiveOddsDispatcher, topic_for_game_id
from mystake.pipeline.notification_processor import NotificationProcessor
from mystake.registry.live_game_registry import LiveGameRegistry
from mystake.sources.cache.client import MystakeCacheClient
from mystake.sources.mqtt.client import ListenerShutdown, MystakeMqttClient
from observe_live_soak import (
    log_and_record_checkpoint,
    schedule_checkpoints,
)
from watch_live_odds import (
    DEFAULT_RECONCILE_INTERVAL_SECONDS,
    build_fixture_lookup,
    install_shutdown_signal_handlers,
    log_outcome,
    print_final_report,
    print_tracked_fixture,
    schedule_reconciliation,
)

logger = logging.getLogger(__name__)

DEFAULT_OBSERVE_SECONDS = 1500.0
DEFAULT_CHECKPOINT_INTERVAL_SECONDS = 60.0

PRIORITY_SPORTS = ("basketball", "tennis")


def select_nonsoccer_game_ids(
    fixtures: tuple[Fixture, ...],
    *,
    max_games: int,
) -> list[int | str]:
    """
    Prioritizes Basketball and Tennis fixtures (in that priority
    order), then fills remaining slots with any other non-Soccer
    sport. Soccer is only used as a last resort if nothing else is
    available, so this run's evidence is non-Soccer-specific.

    No claim is made about which fixture is "closest to ending" -
    MatchTime/MatchStatusID semantics for Basketball/Tennis are not
    yet established (AGENTS.md section 5: no invented field
    semantics), so selection is by sport identity only.
    """

    def sport_name(fixture: Fixture) -> str:
        return fixture.sport.strip().lower() if isinstance(fixture.sport, str) else ""

    selected: list[Fixture] = []

    per_sport_pool = {
        sport: [f for f in fixtures if sport_name(f) == sport]
        for sport in PRIORITY_SPORTS
    }

    # Interleave Basketball/Tennis round-robin so both sports get
    # representation instead of one exhausting all slots first.
    index = 0
    while len(selected) < max_games:
        made_progress = False
        for sport in PRIORITY_SPORTS:
            if len(selected) >= max_games:
                break
            pool = per_sport_pool[sport]
            if index < len(pool):
                selected.append(pool[index])
                made_progress = True
        index += 1
        if not made_progress:
            break

    for fixture in fixtures:
        if len(selected) >= max_games:
            break
        if fixture in selected:
            continue
        if sport_name(fixture) != "soccer":
            selected.append(fixture)

    if len(selected) < max_games:
        for fixture in fixtures:
            if len(selected) >= max_games:
                break
            if fixture not in selected:
                selected.append(fixture)

    return [f.game_id for f in selected]


def print_initial_fixture_state(fixture: Fixture | None, *, game_id: int | str) -> None:
    print()
    print("-" * 80)
    if fixture is None:
        print(f"GameId={game_id!r} (not present in current live discovery)")
        return

    raw = fixture.raw
    print(f"GameId        : {fixture.game_id}")
    print(f"Sport         : {fixture.sport} (sport_id={fixture.sport_id})")
    print(f"Fixture       : {fixture.team1} vs {fixture.team2}")
    print(f"Championship  : {fixture.champ}")
    print(f"Score (disc.) : {raw.get('Score')}")
    print(f"MatchTime     : {raw.get('MatchTime')}")
    print(f"MatchStatusID : {raw.get('MatchStatusID')}")
    print(f"LiveBetStatus : {raw.get('LiveBetStatus')}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--observe-seconds",
        type=float,
        default=DEFAULT_OBSERVE_SECONDS,
        help="bounded observation window in seconds",
    )
    parser.add_argument(
        "--max-games",
        type=int,
        default=LIVE_TRACKED_GAMES_MAX,
        help="max GameIds to auto-select",
    )
    parser.add_argument(
        "--checkpoint-interval-seconds",
        type=float,
        default=DEFAULT_CHECKPOINT_INTERVAL_SECONDS,
        help="how often to log/record a reliability checkpoint",
    )
    parser.add_argument(
        "--reconcile-interval-seconds",
        type=float,
        default=DEFAULT_RECONCILE_INTERVAL_SECONDS,
        help="how often to reconcile tracked GameIds against live discovery",
    )
    parser.add_argument(
        "--report-out",
        type=Path,
        default=None,
        help="path to write the full JSON checkpoint/report evidence file",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="log full raw snapshot payloads (never enable with untrusted output sinks)",
    )
    return parser


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    parser = build_arg_parser()
    args = parser.parse_args()

    cache_client = MystakeCacheClient()
    mqtt_client = MystakeMqttClient()
    install_shutdown_signal_handlers(mqtt_client)

    discovery = LiveFixtureDiscovery(cache_client=cache_client)

    print("Discovering real live fixtures (live/headernew/en)...")
    diff = discovery.refresh()

    if diff is None:
        print("Live discovery FAILED (see logs). Aborting.")
        cache_client.close()
        return

    all_fixtures = discovery.registry.list_all()
    print(f"Total live fixture count: {len(all_fixtures)}")

    sport_counts: dict[str, int] = {}
    for fixture in all_fixtures:
        sport_counts[str(fixture.sport)] = sport_counts.get(str(fixture.sport), 0) + 1
    print("Sport breakdown:")
    for sport, count in sorted(sport_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {sport:<20}: {count}")

    fixture_lookup = build_fixture_lookup(all_fixtures)

    game_ids = select_nonsoccer_game_ids(all_fixtures, max_games=args.max_games)

    if not game_ids:
        print("No live GameIds currently available. Aborting.")
        cache_client.close()
        return

    game_ids = tuple(game_ids)

    print()
    print("=" * 80)
    print(f"TRACKED GAMES ({len(game_ids)}) - initial state")
    print("=" * 80)
    for game_id in game_ids:
        print_initial_fixture_state(fixture_lookup.get(game_id), game_id=game_id)
        print_tracked_fixture(fixture_lookup.get(game_id), game_id=game_id)

    registry = LiveGameRegistry(tracked_game_ids=game_ids)
    notification_processor = NotificationProcessor(cache_client=cache_client)
    dispatcher = LiveOddsDispatcher(
        registry=registry,
        notification_processor=notification_processor,
        mqtt_client=mqtt_client,
    )

    stats = {
        game_id: {
            "notification_count": 0,
            "price_changes": 0,
            "score_changes": 0,
            "selection_changes": 0,
        }
        for game_id in game_ids
    }

    checkpoints: list[dict[str, Any]] = []
    exceptions: list[str] = []
    start = time.monotonic()

    checkpoint_timer = None
    reconcile_timer = None
    observe_timer = None

    try:
        log_and_record_checkpoint(
            "baseline_pre_subscribe", start, registry, mqtt_client, checkpoints
        )

        mqtt_client.connect_with_retry()
        logger.info("MQTT connection established")

        for game_id in game_ids:
            topic = topic_for_game_id(game_id)
            mqtt_client.subscribe(topic)
            logger.info("SUBACK accepted game_id=%s topic=%s", game_id, topic)

        log_and_record_checkpoint(
            "baseline_post_subscribe", start, registry, mqtt_client, checkpoints
        )

        observe_timer = threading.Timer(
            args.observe_seconds, mqtt_client.request_shutdown
        )
        observe_timer.daemon = True
        observe_timer.start()

        checkpoint_timer = schedule_checkpoints(
            mqtt_client,
            registry,
            start,
            checkpoints,
            interval_seconds=args.checkpoint_interval_seconds,
        )

        reconcile_timer = schedule_reconciliation(
            mqtt_client,
            discovery,
            registry,
            interval_seconds=args.reconcile_interval_seconds,
        )

        logger.info(
            "Listening for live notifications on %s tracked game(s) for up to %.0fs",
            len(game_ids),
            args.observe_seconds,
        )

        while True:
            message = mqtt_client.receive_publish()

            outcome = dispatcher.handle(message)

            if outcome is not None:
                log_outcome(
                    outcome.game_id,
                    outcome,
                    stats,
                    debug=args.debug,
                    fixture_lookup=fixture_lookup,
                )

    except (KeyboardInterrupt, ListenerShutdown) as exc:
        logger.info("Stopping (%s)", type(exc).__name__)

    except Exception as exc:
        logger.exception("Stopping due to an unhandled error")
        exceptions.append(f"{type(exc).__name__}: {exc}")

    finally:
        if observe_timer is not None:
            observe_timer.cancel()
        if checkpoint_timer is not None:
            checkpoint_timer.cancel()
        if reconcile_timer is not None:
            reconcile_timer.cancel()

        final_checkpoint = log_and_record_checkpoint(
            "final", start, registry, mqtt_client, checkpoints
        )

        mqtt_client.close()
        cache_client.close()
        logger.info("Resources closed")

        print_final_report(game_ids, stats, registry, fixture_lookup)

        print()
        print("=" * 80)
        print("NON-SOCCER TERMINAL OBSERVATION SUMMARY")
        print("=" * 80)
        print(f"Total elapsed            : {final_checkpoint['elapsed_seconds']}s")
        print(f"Checkpoints recorded     : {len(checkpoints)}")
        print(f"Unhandled exceptions     : {exceptions or 'none'}")
        print(f"Finalized game_ids       : {registry.finalized_game_ids()}")

        if args.report_out is not None:
            args.report_out.parent.mkdir(parents=True, exist_ok=True)
            args.report_out.write_text(
                json.dumps(
                    {
                        "game_ids": [str(g) for g in game_ids],
                        "initial_fixtures": {
                            str(g): (
                                fixture_lookup[g].raw if g in fixture_lookup else None
                            )
                            for g in game_ids
                        },
                        "checkpoints": checkpoints,
                        "unhandled_exceptions": exceptions,
                    },
                    indent=2,
                    default=str,
                )
            )
            print(f"Full checkpoint/report evidence written to: {args.report_out}")


if __name__ == "__main__":
    main()
