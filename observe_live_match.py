"""
Phase 4A: bounded diagnostic observation of ONE real live match.

Reuses the existing live discovery, MQTT, cache, notification, and
diff/event infrastructure end-to-end - it does not introduce a new
parser, MQTT client, or registry. This mirrors `watch_prematch_odds.py`
/ `observe_and_verify_tracked_odds.py`'s bounded-observation pattern:

1. Real `live/headernew/en` discovery (`LiveFixtureDiscovery`).
2. Select one currently available live match (prefers Soccer; falls
   back to any available sport - never a hardcoded/historical GameId
   unless `--game-id` is passed explicitly).
3. Subscribe to the exact topic `live/gamenew/{GameId}` (no wildcard).
4. Wait for real MQTT PUBLISH notifications, decode each via the
   existing cache-indirection pipeline, and diff successive snapshots
   with the existing `diff_live_snapshots` / `map_live_diff_to_events`.
5. Bounded by wall-clock time (`--observe-seconds`, default 180s),
   using the same `request_shutdown()` mechanism (forces the blocking
   socket closed) as the other listeners' graceful-shutdown handling -
   SIGINT also works at any point.

Usage:

    uv run python -u observe_live_match.py
    uv run python -u observe_live_match.py --game-id 76335839
    uv run python -u observe_live_match.py --observe-seconds 240
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import threading
from pathlib import Path
from typing import Any

from mystake.events.mapper import map_live_diff_to_events
from mystake.models.fixture import Fixture
from mystake.models.snapshot import parse_live_snapshot
from mystake.pipeline.live_discovery import LiveFixtureDiscovery
from mystake.pipeline.live_snapshot_diff import diff_live_snapshots
from mystake.pipeline.notification_processor import NotificationProcessor
from mystake.sources.cache.client import MystakeCacheClient
from mystake.sources.mqtt.client import ListenerShutdown, MystakeMqttClient

logger = logging.getLogger(__name__)

DEFAULT_OBSERVE_SECONDS = 180.0

# Raw per-Games-entry fields whose semantics are UNKNOWN (docs/product/
# SCHEMA.md section 1a/4) - printed verbatim, never interpreted.
RAW_STATUS_FIELD_NAMES = (
    "MatchStatusID",
    "ls",
    "bgid",
    "mc",
    "neut",
    "plng",
    "ovlng",
    "hst",
    "tdesc",
    "hprs",
    "rct1",
    "rct2",
    "bgenid",
    "MatchTime",
    "Score",
    "LiveBetStatus",
)


def parse_game_id(raw: str) -> int | str:
    try:
        return int(raw)
    except ValueError:
        return raw


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--game-id",
        type=parse_game_id,
        default=None,
        help="explicit GameId to observe (skips auto-selection)",
    )
    parser.add_argument(
        "--observe-seconds",
        type=float,
        default=DEFAULT_OBSERVE_SECONDS,
        help="bounded observation window in seconds",
    )
    parser.add_argument(
        "--snapshot-out",
        type=Path,
        default=None,
        help="path to write the sanitized initial snapshot JSON",
    )
    return parser


def print_fixture_row(fixture: Fixture) -> None:
    print(
        f"  GameId={fixture.game_id!r:<12} "
        f"Sport={fixture.sport!r:<20} "
        f"Region={fixture.region!r:<20} "
        f"Champ={fixture.champ!r:<30} "
        f"Team1={fixture.team1!r:<25} "
        f"Team2={fixture.team2!r}"
    )


def select_live_fixture(
    fixtures: tuple[Fixture, ...],
    *,
    preferred_game_id: int | str | None,
) -> Fixture | None:
    if preferred_game_id is not None:
        for fixture in fixtures:
            if fixture.game_id == preferred_game_id:
                return fixture
        return None

    for fixture in fixtures:
        if isinstance(fixture.sport, str) and fixture.sport.strip().lower() == "soccer":
            return fixture

    return fixtures[0] if fixtures else None


def print_selected_fixture(fixture: Fixture) -> None:
    print()
    print("=" * 80)
    print("SELECTED LIVE FIXTURE")
    print("=" * 80)
    print(f"GameId       : {fixture.game_id}")
    print(f"Sport        : {fixture.sport} (sport_id={fixture.sport_id})")
    print(f"Region       : {fixture.region} (region_id={fixture.region_id})")
    print(f"Championship : {fixture.champ} (champ_id={fixture.champ_id})")
    print(f"Team1        : {fixture.team1} (team1_id={fixture.team1_id})")
    print(f"Team2        : {fixture.team2} (team2_id={fixture.team2_id})")
    print()
    print("Raw status fields (UNKNOWN semantics unless noted, preserved verbatim):")
    for name in RAW_STATUS_FIELD_NAMES:
        if name in fixture.raw:
            print(f"  {name:<15}: {fixture.raw[name]!r}")


def print_snapshot_summary(data: dict[str, Any], *, label: str) -> None:
    snapshot = parse_live_snapshot(data)
    match = data.get("Match") if isinstance(data.get("Match"), dict) else {}

    print()
    print("=" * 80)
    print(label)
    print("=" * 80)
    print(f"GameId        : {snapshot.game_id}")
    print(f"Score         : {match.get('Score')}")
    print(f"GameScore     : {match.get('GameScore')}")
    print(f"MatchTime     : {match.get('MatchTime')}")
    print(f"MatchTimeExt  : {match.get('MatchTimeExtended')}")
    print(f"Status        : {match.get('Status')}")
    print(f"BetStatus     : {match.get('BetStatus')}")
    print(f"EventStatus   : {match.get('EventStatus')}")
    print(f"LiveBetStatus : {match.get('LiveBetStatus')}")
    print(f"Market count  : {len(snapshot.markets)}")

    selection_count = sum(len(market.selections) for market in snapshot.markets)
    print(f"Selection count: {selection_count}")

    print()
    print("Sample markets/selections/odds:")
    shown = 0
    for market in snapshot.markets:
        for selection in market.selections:
            print(
                f"  market={market.id} selection={selection.id} "
                f"price={selection.price} visible={selection.visible}"
            )
            shown += 1
            if shown >= 10:
                break
        if shown >= 10:
            break

    if shown == 0:
        print("  (no selections present in this snapshot)")


def print_diff(diff, stats: dict[str, int]) -> None:
    print()
    print("=" * 80)
    print("LIVE SNAPSHOT DIFF")
    print("=" * 80)

    if not diff.has_changes:
        print("No changes detected between successive snapshots.")
        return

    if diff.match_changes:
        print()
        print("MATCH_TIME_CHANGED / MATCH_STATUS_CHANGED / SCORE_CHANGED fields:")
        for change in diff.match_changes:
            print(f"  {change.field}: {change.old!r} -> {change.new!r}")
            if change.field in ("Score", "GameScore"):
                stats["score_changes"] += 1

    if diff.added_selections:
        print()
        print(f"SELECTION_ADDED ({len(diff.added_selections)}):")
        for change in diff.added_selections[:10]:
            print(f"  market={change.market_id} selection={change.selection_id}")
        stats["selection_changes"] += len(diff.added_selections)

    if diff.removed_selections:
        print()
        print(f"SELECTION_REMOVED ({len(diff.removed_selections)}):")
        for change in diff.removed_selections[:10]:
            print(f"  market={change.market_id} selection={change.selection_id}")
        stats["selection_changes"] += len(diff.removed_selections)

    if diff.price_changes:
        print()
        print(f"PRICE_CHANGED ({len(diff.price_changes)}):")
        for change in diff.price_changes:
            print(
                f"  GameId={stats.get('game_id')} "
                f"MarketId={change.market_id} SelectionId={change.selection_id} "
                f"PreviousPrice={change.old} NewPrice={change.new}"
            )
        stats["price_changes"] += len(diff.price_changes)

    if diff.visibility_changes:
        print()
        print(f"VISIBILITY_CHANGED ({len(diff.visibility_changes)}):")
        for change in diff.visibility_changes[:10]:
            print(
                f"  market={change.market_id} selection={change.selection_id} "
                f"{change.old!r} -> {change.new!r}"
            )

    if diff.new_timeline_items:
        print()
        print(f"NEW TIMELINE ITEMS ({len(diff.new_timeline_items)}):")
        for item in diff.new_timeline_items[:5]:
            print(f"  {item}")

    if diff.match_ended:
        print()
        print("MATCH_ENDED transition detected.")
        for change in diff.match_ended_context:
            print(f"  {change.field}: {change.old!r} -> {change.new!r}")


def install_shutdown_signal_handlers(mqtt_client: MystakeMqttClient) -> None:
    def handle_shutdown_signal(signum: int, frame: object) -> None:
        logger.info(
            "Received signal=%s; requesting listener shutdown",
            signal.Signals(signum).name,
        )
        mqtt_client.request_shutdown()

    signal.signal(signal.SIGINT, handle_shutdown_signal)
    signal.signal(signal.SIGTERM, handle_shutdown_signal)


def sanitize_for_json(value: Any) -> Any:
    """
    Best-effort JSON-safe conversion (handles Decimal via str fallback)
    without altering the underlying values - used only for the saved
    diagnostic snapshot file.
    """
    return json.loads(json.dumps(value, default=str))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    parser = build_arg_parser()
    args = parser.parse_args()

    mqtt_client = MystakeMqttClient()
    cache_client = MystakeCacheClient()

    install_shutdown_signal_handlers(mqtt_client)

    discovery = LiveFixtureDiscovery(cache_client=cache_client)

    stats = {
        "publish_count": 0,
        "decoded_count": 0,
        "price_changes": 0,
        "score_changes": 0,
        "selection_changes": 0,
        "game_id": None,
    }

    timer: threading.Timer | None = None

    try:
        print("Discovering real live fixtures (live/headernew/en)...")
        diff = discovery.refresh()

        if diff is None:
            print("Live discovery FAILED (see logs). Aborting.")
            return

        all_fixtures = discovery.registry.list_all()
        print(f"Total live fixture count: {len(all_fixtures)}")

        print()
        print("Sample of discovered live fixtures (first 10):")
        for fixture in all_fixtures[:10]:
            print_fixture_row(fixture)

        selected = select_live_fixture(all_fixtures, preferred_game_id=args.game_id)

        if selected is None:
            print()
            if args.game_id is not None:
                print(
                    f"Requested --game-id={args.game_id} not found in live discovery. Aborting."
                )
            else:
                print("No live fixtures currently available. Aborting.")
            return

        stats["game_id"] = selected.game_id
        print_selected_fixture(selected)

        topic = f"live/gamenew/{selected.game_id}"

        print()
        print(f"Connecting to MQTT and subscribing topic={topic} ...")
        mqtt_client.connect_with_retry()
        print("MQTT connection established.")

        mqtt_client.subscribe(topic)
        print(f"SUBACK accepted for topic={topic}.")

        timer = threading.Timer(args.observe_seconds, mqtt_client.request_shutdown)
        timer.daemon = True
        timer.start()
        print(
            f"Observing for up to {args.observe_seconds:.0f}s "
            "(SIGINT also stops cleanly)..."
        )

        processor = NotificationProcessor(cache_client=cache_client)
        previous_snapshot: dict[str, Any] | None = None
        snapshot_path = args.snapshot_out

        while True:
            message = mqtt_client.receive_publish()
            stats["publish_count"] += 1

            print()
            print(f"--- PUBLISH #{stats['publish_count']} topic={message.topic} ---")

            if message.topic != topic:
                print(f"Ignoring PUBLISH on unrelated topic={message.topic}")
                continue

            try:
                result = processor.process(message)
            except Exception:
                logger.exception("Failed to process PUBLISH; skipping")
                continue

            if not isinstance(result.data, dict):
                logger.warning(
                    "Ignoring non-dict live snapshot type=%s",
                    type(result.data).__name__,
                )
                continue

            stats["decoded_count"] += 1

            if previous_snapshot is None:
                print_snapshot_summary(result.data, label="INITIAL LIVE SNAPSHOT")

                if snapshot_path is not None:
                    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
                    snapshot_path.write_text(
                        json.dumps(
                            sanitize_for_json(result.data),
                            indent=2,
                        )
                    )
                    print(f"\nSaved sanitized raw snapshot to: {snapshot_path}")
            else:
                snapshot_diff = diff_live_snapshots(previous_snapshot, result.data)
                print_diff(snapshot_diff, stats)

                events = map_live_diff_to_events(snapshot_diff, result.data)
                if events:
                    print()
                    print("DOMAIN EVENTS")
                    for event in events:
                        print(
                            f"  {event.event_type.value} game_id={event.game_id} {event.payload}"
                        )

                if snapshot_diff.match_ended:
                    print()
                    print("Match ended; stopping observation early.")
                    mqtt_client.unsubscribe(topic)
                    previous_snapshot = None
                    break

            previous_snapshot = result.data

    except (KeyboardInterrupt, ListenerShutdown) as exc:
        print()
        print(f"Observation window ended ({type(exc).__name__}).")

    finally:
        if timer is not None:
            timer.cancel()

        mqtt_client.close()
        cache_client.close()

        print()
        print("=" * 80)
        print("FINAL SUMMARY")
        print("=" * 80)
        print(f"GameId observed          : {stats['game_id']}")
        print(f"MQTT PUBLISH count       : {stats['publish_count']}")
        print(f"Decoded snapshot count   : {stats['decoded_count']}")
        print(f"Price changes            : {stats['price_changes']}")
        print(f"Score changes            : {stats['score_changes']}")
        print(f"Selection add/remove     : {stats['selection_changes']}")

        if stats["price_changes"] == 0:
            print()
            print("REAL LIVE ODDS CHANGE: NOT VERIFIED")


if __name__ == "__main__":
    main()
