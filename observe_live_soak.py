"""
Phase 6A: bounded real-network soak observation of sustained live
collector reliability.

Reuses existing infrastructure end-to-end - live discovery
(`LiveFixtureDiscovery`), the MQTT client
(`mystake.sources.mqtt.client.MystakeMqttClient`), the live game
registry/dispatcher (`LiveGameRegistry`/`LiveOddsDispatcher`), and
`watch_live_odds.py`'s auto-selection/reconciliation/reporting helpers.
It adds only the periodic checkpoint instrumentation Phase 6A calls
for (process memory, MQTT client internal queue/waiter counts,
per-game snapshot freshness) - no new transport, parsing, or registry
code.

Usage:

    uv run python -u observe_live_soak.py --observe-seconds 1800
    uv run python -u observe_live_soak.py --observe-seconds 300 --checkpoint-interval-seconds 30
"""

from __future__ import annotations

import argparse
import json
import logging
import resource
import sys
import threading
import time
from pathlib import Path
from typing import Any

from mystake.config import LIVE_TRACKED_GAMES_MAX
from mystake.pipeline.live_discovery import LiveFixtureDiscovery
from mystake.pipeline.live_odds_dispatcher import LiveOddsDispatcher, topic_for_game_id
from mystake.pipeline.notification_processor import NotificationProcessor
from mystake.registry.live_game_registry import LiveGameRegistry
from mystake.sources.cache.client import MystakeCacheClient
from mystake.sources.mqtt.client import ListenerShutdown, MystakeMqttClient
from watch_live_odds import (
    DEFAULT_RECONCILE_INTERVAL_SECONDS,
    build_fixture_lookup,
    install_shutdown_signal_handlers,
    log_outcome,
    print_final_report,
    print_tracked_fixture,
    schedule_reconciliation,
    select_auto_game_ids,
)

logger = logging.getLogger(__name__)

DEFAULT_OBSERVE_SECONDS = 1800.0
DEFAULT_CHECKPOINT_INTERVAL_SECONDS = 120.0


def peak_rss_bytes() -> int:
    """
    Process peak resident set size, stdlib-only (no new dependency).
    `ru_maxrss` is bytes on macOS (this project's dev platform) and
    KiB on Linux.
    """
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return usage if sys.platform == "darwin" else usage * 1024


def mqtt_internal_snapshot(mqtt_client: MystakeMqttClient) -> dict[str, Any]:
    with mqtt_client._ack_lock:
        ack_waiters = len(mqtt_client._ack_waiters)

    return {
        "connected": mqtt_client.websocket is not None,
        "subscription_count": len(mqtt_client._subscriptions),
        "pending_packet_count": len(mqtt_client._pending_packets),
        "ack_waiter_count": ack_waiters,
    }


def build_checkpoint(
    label: str,
    start: float,
    registry: LiveGameRegistry,
    mqtt_client: MystakeMqttClient,
) -> dict[str, Any]:
    per_game = {}

    for game_id in registry.tracked_game_ids():
        state = registry.get(game_id)
        assert state is not None

        per_game[str(game_id)] = {
            "lifecycle_state": str(state.lifecycle_state),
            "notification_count": state.notification_count,
            "last_notified_at": state.last_notified_at,
            "last_error": state.last_error,
            "missing_from_discovery_streak": state.missing_from_discovery_streak,
        }

    record: dict[str, Any] = {
        "label": label,
        "elapsed_seconds": round(time.monotonic() - start, 1),
        "wall_clock": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "peak_rss_bytes": peak_rss_bytes(),
        "active_game_ids": [str(g) for g in registry.active_game_ids()],
        "finalized_game_ids": [str(g) for g in registry.finalized_game_ids()],
        "per_game": per_game,
    }
    record.update(mqtt_internal_snapshot(mqtt_client))

    return record


def log_and_record_checkpoint(
    label: str,
    start: float,
    registry: LiveGameRegistry,
    mqtt_client: MystakeMqttClient,
    checkpoints: list[dict[str, Any]],
) -> dict[str, Any]:
    record = build_checkpoint(label, start, registry, mqtt_client)
    checkpoints.append(record)
    logger.info("CHECKPOINT[%s] %s", label, json.dumps(record, default=str))
    return record


def schedule_checkpoints(
    mqtt_client: MystakeMqttClient,
    registry: LiveGameRegistry,
    start: float,
    checkpoints: list[dict[str, Any]],
    *,
    interval_seconds: float,
) -> threading.Timer | None:
    if interval_seconds <= 0 or mqtt_client.shutdown_requested:
        return None

    def _tick() -> None:
        if mqtt_client.shutdown_requested:
            return

        log_and_record_checkpoint(
            "periodic",
            start,
            registry,
            mqtt_client,
            checkpoints,
        )

        schedule_checkpoints(
            mqtt_client,
            registry,
            start,
            checkpoints,
            interval_seconds=interval_seconds,
        )

    timer = threading.Timer(interval_seconds, _tick)
    timer.daemon = True
    timer.start()
    return timer


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
    fixture_lookup = build_fixture_lookup(all_fixtures)

    game_ids = select_auto_game_ids(all_fixtures, max_games=args.max_games)

    if not game_ids:
        print("No live GameIds currently available. Aborting.")
        cache_client.close()
        return

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

    checkpoints: list[dict[str, Any]] = []
    exceptions: list[str] = []
    start = time.monotonic()

    checkpoint_timer: threading.Timer | None = None
    reconcile_timer: threading.Timer | None = None
    observe_timer: threading.Timer | None = None

    try:
        # Baseline (Step 2), captured before subscribing so
        # subscription_count=0 is on record.
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
        print("SOAK RELIABILITY SUMMARY")
        print("=" * 80)
        print(f"Total elapsed            : {final_checkpoint['elapsed_seconds']}s")
        print(f"Checkpoints recorded     : {len(checkpoints)}")
        print(f"Unhandled exceptions     : {exceptions or 'none'}")

        if args.report_out is not None:
            args.report_out.parent.mkdir(parents=True, exist_ok=True)
            args.report_out.write_text(
                json.dumps(
                    {
                        "game_ids": [str(g) for g in game_ids],
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
