"""
Phase 4B: bounded multi-game real live odds tracking.

Reuses the existing live discovery, MQTT, cache-indirection,
notification, and diff/event infrastructure end-to-end (Phase 4A) -
it only adds the orchestration needed to track several GameIds at
once, each with fully independent state:

1. Real `live/headernew/en` discovery (`LiveFixtureDiscovery`), used
   to enrich reporting (sport/fixture names) and, if no `--game-id`
   was passed, to auto-select a bounded set of currently live
   fixtures (preferring more than one sport when available).
2. An exact MQTT subscription per tracked GameId -
   `live/gamenew/{GameId}` - never a wildcard subscription (rejected
   by the broker with SUBACK 0x80; see docs/product/SCHEMA.md
   section 3).
3. Each PUBLISH is routed by its exact topic to that GameId's own
   entry in a `LiveGameRegistry` (`LiveOddsDispatcher`) - a
   notification for one GameId never touches another tracked GameId's
   state.
4. Existing `diff_live_snapshots` / `map_live_diff_to_events` per
   game.
5. Bounded by wall-clock time (`--observe-seconds`), using the same
   `request_shutdown()` mechanism as the other listeners - SIGINT/
   SIGTERM also work at any point. Reconnects resubscribe to every
   tracked topic (`MystakeMqttClient._reconnect_and_resubscribe`).

Usage:

    uv run python -u watch_live_odds.py
    uv run python -u watch_live_odds.py --game-id 75832139 --game-id 76335839
    uv run python -u watch_live_odds.py --max-games 3 --observe-seconds 240
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading

from mystake.config import LIVE_TRACKED_GAMES_MAX
from mystake.models.fixture import Fixture
from mystake.pipeline.live_discovery import LiveFixtureDiscovery
from mystake.pipeline.live_market_enrichment import (
    SelectionMetadata,
    build_selection_metadata_lookup,
)
from mystake.pipeline.live_odds_dispatcher import (
    LiveOddsDispatcher,
    topic_for_game_id,
)
from mystake.pipeline.notification_processor import NotificationProcessor
from mystake.registry.live_game_registry import (
    LiveApplyOutcome,
    LiveGameRegistry,
    LiveLifecycleState,
)
from mystake.sources.cache.client import MystakeCacheClient
from mystake.sources.mqtt.client import ListenerShutdown, MystakeMqttClient

logger = logging.getLogger(__name__)

DEFAULT_OBSERVE_SECONDS = 240.0
DEFAULT_RECONCILE_INTERVAL_SECONDS = 60.0


def parse_game_id(raw: str) -> int | str:
    try:
        return int(raw)
    except ValueError:
        return raw


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--game-id",
        dest="game_ids",
        action="append",
        default=None,
        help=(
            "GameId to track; may be passed multiple times "
            f"(max {LIVE_TRACKED_GAMES_MAX}). If omitted, GameIds are "
            "auto-selected from real live discovery."
        ),
    )
    parser.add_argument(
        "--max-games",
        type=int,
        default=LIVE_TRACKED_GAMES_MAX,
        help="max GameIds to auto-select when --game-id is omitted",
    )
    parser.add_argument(
        "--observe-seconds",
        type=float,
        default=DEFAULT_OBSERVE_SECONDS,
        help="bounded observation window in seconds",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="log full raw snapshot payloads (never enable with untrusted output sinks)",
    )
    parser.add_argument(
        "--reconcile-interval-seconds",
        type=float,
        default=DEFAULT_RECONCILE_INTERVAL_SECONDS,
        help=(
            "how often to re-fetch live/headernew/en and reconcile tracked "
            "GameIds against it (Phase 4D Step 5); disappearance alone never "
            "finalizes a GameId, it only flags it UNKNOWN for reporting. Set "
            "<=0 to disable periodic reconciliation."
        ),
    )

    return parser


def select_auto_game_ids(
    fixtures: tuple[Fixture, ...],
    *,
    max_games: int,
) -> list[int | str]:
    """
    Prefers a diverse set of sports (one fixture per distinct sport
    first, in discovery order), then fills any remaining slots with
    additional fixtures, bounded by `max_games`.
    """
    selected: list[Fixture] = []
    seen_sports: set[object] = set()

    for fixture in fixtures:
        if len(selected) >= max_games:
            break
        if fixture.sport not in seen_sports:
            selected.append(fixture)
            seen_sports.add(fixture.sport)

    for fixture in fixtures:
        if len(selected) >= max_games:
            break
        if fixture not in selected:
            selected.append(fixture)

    return [fixture.game_id for fixture in selected]


def print_tracked_fixture(fixture: Fixture | None, *, game_id: int | str) -> None:
    if fixture is None:
        print(f"  GameId={game_id!r:<12} (not present in current live discovery)")
        return

    print(
        f"  GameId={fixture.game_id!r:<12} "
        f"Sport={fixture.sport!r:<15} "
        f"Champ={fixture.champ!r:<25} "
        f"{fixture.team1} vs {fixture.team2}"
    )


def build_fixture_lookup(
    fixtures: tuple[Fixture, ...],
) -> dict[int | str, Fixture]:
    return {fixture.game_id: fixture for fixture in fixtures}


def snapshot_summary(data: dict, *, debug: bool) -> str:
    match = data.get("Match") if isinstance(data.get("Match"), dict) else {}
    gmk = data.get("gmk")
    selection_count = len(gmk) if isinstance(gmk, list) else 0
    market_ids = (
        {item.get("mid") for item in gmk if isinstance(item, dict)}
        if isinstance(gmk, list)
        else set()
    )

    summary = (
        f"score={match.get('Score')} time={match.get('MatchTime')} "
        f"markets={len(market_ids)} selections={selection_count}"
    )

    if debug:
        summary += f" raw={data}"

    return summary


def format_price_change_block(
    game_id: int | str,
    change,
    metadata: SelectionMetadata | None,
    fixture: Fixture | None,
) -> str:
    """
    Renders one enriched real price change for display (Phase 4C).
    `metadata`/`fixture` are looked up per-change and may be `None`
    (untracked market/selection, or fixture not present in the latest
    discovery) - real gaps are shown as `UNKNOWN`, never guessed.
    """
    fixture_desc = (
        f"{fixture.team1} vs {fixture.team2}" if fixture is not None else "UNKNOWN"
    )
    market_name = metadata.market_name if metadata is not None else None
    selection_name = metadata.selection_name if metadata is not None else None
    line = metadata.line if metadata is not None else None

    market_display = market_name if market_name is not None else "UNKNOWN"
    selection_display = selection_name if selection_name is not None else "UNKNOWN"

    lines = [
        f"GAME: {game_id}",
        f"FIXTURE: {fixture_desc}",
        f"MARKET: {change.market_id} | {market_display}",
        f"SELECTION: {change.selection_id} | {selection_display}",
    ]

    if line is not None:
        lines.append(f"LINE: {line}")

    lines.append(f"PRICE: {change.old} -> {change.new}")

    return "\n".join(lines)


def log_outcome(
    game_id: int | str,
    outcome: LiveApplyOutcome | None,
    stats: dict[int | str, dict[str, int]],
    *,
    debug: bool,
    fixture_lookup: dict[int | str, Fixture] | None = None,
) -> None:
    if outcome is None:
        return

    if outcome.ignored_after_finalization:
        logger.info(
            "game_id=%s late/stale notification ignored (already finalized)",
            game_id,
        )
        return

    game_stats = stats[game_id]
    game_stats["notification_count"] += 1

    if outcome.is_initial:
        logger.info(
            "game_id=%s INITIAL live snapshot: %s",
            game_id,
            snapshot_summary(outcome.snapshot, debug=debug),
        )
        return

    if outcome.duplicate:
        logger.info("game_id=%s duplicate notification (no changes to apply)", game_id)
        return

    diff = outcome.diff

    if diff is None or not diff.has_changes:
        logger.info("game_id=%s snapshot unchanged", game_id)
        return

    game_stats["price_changes"] += len(diff.price_changes)
    game_stats["selection_changes"] += len(diff.added_selections) + len(
        diff.removed_selections
    )

    score_field_names = {"Score", "GameScore"}
    game_stats["score_changes"] += sum(
        1 for change in diff.match_changes if change.field in score_field_names
    )

    logger.info(
        "game_id=%s UPDATE price_changes=%s added_selections=%s "
        "removed_selections=%s match_changes=%s",
        game_id,
        len(diff.price_changes),
        len(diff.added_selections),
        len(diff.removed_selections),
        [f"{c.field}: {c.old!r}->{c.new!r}" for c in diff.match_changes],
    )

    if diff.price_changes:
        metadata_lookup = build_selection_metadata_lookup(outcome.snapshot)
        fixture = fixture_lookup.get(game_id) if fixture_lookup is not None else None

        for change in diff.price_changes[:10]:
            block = format_price_change_block(
                game_id,
                change,
                metadata_lookup.get(change.selection_id),
                fixture,
            )
            logger.info("Real live odds change:\n%s", block)

    if diff.match_ended:
        logger.info("game_id=%s MATCH_ENDED transition detected", game_id)


def schedule_reconciliation(
    mqtt_client: MystakeMqttClient,
    discovery: LiveFixtureDiscovery,
    registry: LiveGameRegistry,
    *,
    interval_seconds: float,
) -> threading.Timer | None:
    """
    Bounded, periodic Step 5 reconciliation: re-fetches real
    `live/headernew/en` (reusing the existing `LiveFixtureDiscovery`,
    no new HTTP/parsing path) and reconciles it against tracked
    GameIds via `LiveGameRegistry.reconcile_discovery`. Disappearance
    from discovery only flags a GameId UNKNOWN for reporting - it is
    never treated as match completion. Self-reschedules until listener
    shutdown is requested, bounding total reconciliation work to the
    same wall-clock window as the observation itself.
    """
    if interval_seconds <= 0 or mqtt_client.shutdown_requested:
        return None

    def _tick() -> None:
        if mqtt_client.shutdown_requested:
            return

        diff = discovery.refresh()

        if diff is None:
            logger.warning(
                "Reconciliation: live/headernew/en refresh failed; "
                "preserving previous lifecycle state for all tracked games"
            )
        else:
            live_game_ids = {
                fixture.game_id for fixture in discovery.registry.list_all()
            }
            result = registry.reconcile_discovery(live_game_ids)

            if result.newly_unknown:
                logger.warning(
                    "Reconciliation: game_id(s) %s missing from live discovery "
                    "(flagged UNKNOWN, NOT finalized)",
                    result.newly_unknown,
                )
            if result.recovered:
                logger.info(
                    "Reconciliation: game_id(s) %s reappeared in live discovery "
                    "(back to ACTIVE)",
                    result.recovered,
                )
            if result.still_unknown:
                logger.info(
                    "Reconciliation: game_id(s) %s still missing from live discovery",
                    result.still_unknown,
                )

        schedule_reconciliation(
            mqtt_client,
            discovery,
            registry,
            interval_seconds=interval_seconds,
        )

    timer = threading.Timer(interval_seconds, _tick)
    timer.daemon = True
    timer.start()
    return timer


def run(
    mqtt_client: MystakeMqttClient,
    dispatcher: LiveOddsDispatcher,
    game_ids: tuple[int | str, ...],
    stats: dict[int | str, dict[str, int]],
    *,
    observe_seconds: float,
    debug: bool,
    fixture_lookup: dict[int | str, Fixture] | None = None,
    discovery: LiveFixtureDiscovery | None = None,
    reconcile_interval_seconds: float = DEFAULT_RECONCILE_INTERVAL_SECONDS,
) -> None:
    mqtt_client.connect_with_retry()
    logger.info("MQTT connection established")

    for game_id in game_ids:
        if mqtt_client.shutdown_requested:
            logger.info("Shutdown requested; aborting remaining subscriptions")
            return

        topic = topic_for_game_id(game_id)
        mqtt_client.subscribe(topic)
        logger.info("SUBACK accepted game_id=%s topic=%s", game_id, topic)

    timer = threading.Timer(observe_seconds, mqtt_client.request_shutdown)
    timer.daemon = True
    timer.start()

    reconcile_timer = (
        schedule_reconciliation(
            mqtt_client,
            discovery,
            dispatcher.registry,
            interval_seconds=reconcile_interval_seconds,
        )
        if discovery is not None
        else None
    )

    try:
        logger.info(
            "Listening for live notifications on %s tracked game(s) for up to %.0fs",
            len(game_ids),
            observe_seconds,
        )

        while True:
            message = mqtt_client.receive_publish()

            outcome = dispatcher.handle(message)

            if outcome is not None:
                log_outcome(
                    outcome.game_id,
                    outcome,
                    stats,
                    debug=debug,
                    fixture_lookup=fixture_lookup,
                )

    finally:
        timer.cancel()
        if reconcile_timer is not None:
            reconcile_timer.cancel()


def serve(
    mqtt_client: MystakeMqttClient,
    dispatcher: LiveOddsDispatcher,
    game_ids: tuple[int | str, ...],
    stats: dict[int | str, dict[str, int]],
    cache_client: MystakeCacheClient,
    *,
    observe_seconds: float,
    debug: bool,
    fixture_lookup: dict[int | str, Fixture] | None = None,
    discovery: LiveFixtureDiscovery | None = None,
    reconcile_interval_seconds: float = DEFAULT_RECONCILE_INTERVAL_SECONDS,
) -> None:
    try:
        run(
            mqtt_client,
            dispatcher,
            game_ids,
            stats,
            observe_seconds=observe_seconds,
            debug=debug,
            fixture_lookup=fixture_lookup,
            discovery=discovery,
            reconcile_interval_seconds=reconcile_interval_seconds,
        )

    except (KeyboardInterrupt, ListenerShutdown) as exc:
        logger.info("Listener stopping (%s)", type(exc).__name__)

    except Exception:
        logger.exception("Listener stopping due to an unhandled error")
        raise

    finally:
        mqtt_client.close()
        cache_client.close()
        logger.info("Listener stopped; resources closed")


def install_shutdown_signal_handlers(mqtt_client: MystakeMqttClient) -> None:
    def handle_shutdown_signal(signum: int, frame: object) -> None:
        logger.info(
            "Received signal=%s; requesting listener shutdown",
            signal.Signals(signum).name,
        )
        mqtt_client.request_shutdown()

    signal.signal(signal.SIGINT, handle_shutdown_signal)
    signal.signal(signal.SIGTERM, handle_shutdown_signal)


def print_final_report(
    game_ids: tuple[int | str, ...],
    stats: dict[int | str, dict[str, int]],
    registry: LiveGameRegistry,
    fixture_lookup: dict[int | str, Fixture],
) -> None:
    print()
    print("=" * 80)
    print("FINAL SUMMARY (per tracked GameId)")
    print("=" * 80)

    for game_id in game_ids:
        game_stats = stats[game_id]
        state = registry.get(game_id)
        fixture = fixture_lookup.get(game_id)

        print()
        print(f"GameId={game_id}")
        if fixture is not None:
            print(f"  Sport={fixture.sport} Fixture={fixture.team1} vs {fixture.team2}")
        print(f"  Notifications received : {game_stats['notification_count']}")
        print(f"  Price changes          : {game_stats['price_changes']}")
        print(f"  Score changes          : {game_stats['score_changes']}")
        print(f"  Selection add/remove   : {game_stats['selection_changes']}")

        if state is not None and state.snapshot is not None:
            print(
                f"  Last snapshot          : {snapshot_summary(state.snapshot, debug=False)}"
            )
        else:
            print("  Last snapshot          : (none received)")

        if state is not None:
            print(f"  Lifecycle state        : {state.lifecycle_state}")
            if state.lifecycle_state is LiveLifecycleState.TERMINAL:
                print(f"  Finalized at           : {state.finalized_at}")
            if state.missing_from_discovery_since is not None:
                print(
                    "  Missing from discovery : since "
                    f"{state.missing_from_discovery_since} "
                    f"(streak={state.missing_from_discovery_streak})"
                )

        if state is not None and state.last_error is not None:
            print(f"  Last error             : {state.last_error}")

        if game_stats["price_changes"] == 0:
            print("  REAL LIVE ODDS CHANGE: NOT VERIFIED for this GameId")

        if state is None or state.lifecycle_state is not LiveLifecycleState.TERMINAL:
            print("  MATCH_END TRANSITION: NOT VERIFIED for this GameId")


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
    fixture_lookup = build_fixture_lookup(all_fixtures)

    if args.game_ids:
        game_ids = [parse_game_id(raw) for raw in dict.fromkeys(args.game_ids)]
    else:
        game_ids = select_auto_game_ids(all_fixtures, max_games=args.max_games)

    if not game_ids:
        print("No GameIds selected/available. Aborting.")
        cache_client.close()
        return

    if len(game_ids) > LIVE_TRACKED_GAMES_MAX:
        cache_client.close()
        parser.error(
            f"at most {LIVE_TRACKED_GAMES_MAX} tracked GameIds are "
            f"supported, got {len(game_ids)}"
        )

    game_ids = tuple(game_ids)

    print()
    print("=" * 80)
    print(f"TRACKED GAMES ({len(game_ids)})")
    print("=" * 80)
    for game_id in game_ids:
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

    serve(
        mqtt_client,
        dispatcher,
        game_ids,
        stats,
        cache_client,
        fixture_lookup=fixture_lookup,
        observe_seconds=args.observe_seconds,
        debug=args.debug,
        discovery=discovery,
        reconcile_interval_seconds=args.reconcile_interval_seconds,
    )

    print_final_report(game_ids, stats, registry, fixture_lookup)


if __name__ == "__main__":
    main()
