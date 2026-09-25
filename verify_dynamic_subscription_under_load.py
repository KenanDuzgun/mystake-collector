"""
Phase 5C final verification: dynamic subscription while the main
thread is actively blocking in `ws.recv()`.

Reuses existing live discovery, MQTT, cache-indirection, notification,
and registry/dispatcher infrastructure unmodified. Adds only the
orchestration needed to prove the exact scenario Phase 5C targets:

1. Connect and subscribe to one real live GameId (`live/gamenew/{id}`).
2. Start the normal `receive_publish()` loop on the main thread - this
   is the thread that ends up blocked inside `ws.recv()` between
   PUBLISH messages (bounded by the read timeout /
   `MQTT_KEEP_ALIVE_SECONDS // 2`).
3. From a background thread, after a short delay to let the main
   thread reach its blocking read, dynamically `subscribe()` to one or
   more additional real live GameIds.
4. Confirm from the client's own instrumentation (already emitted by
   `_send_and_await_ack`/`_dispatch_ack`, unmodified in this script)
   that:
   - the background SUBSCRIBE's non-blocking `_reader_lock.acquire()`
     failed (`became_reader=False`) - i.e. the main thread already
     held the socket-read role at that moment, so the background
     caller did not itself pump `ws.recv()`.
   - the physical send (`SEND_LOCK_ACQUIRED` -> `PACKET_SENT`) happened
     promptly, not after a read-timeout-length wait.
   - SUBACK for the new packet_id was dispatched and the newly
     subscribed GameId(s) received a valid initial snapshot.
   - the original tracked GameId kept receiving PUBLISH updates
     throughout.

Bounded by wall-clock time (`--observe-seconds`); no unbounded
polling/retry (AGENTS.md section 4).

Usage:

    uv run python -u verify_dynamic_subscription_under_load.py
    uv run python -u verify_dynamic_subscription_under_load.py \
        --observe-seconds 60 --subscribe-after-seconds 5
"""

from __future__ import annotations

import argparse
import logging
import threading
import time

from mystake.config import LIVE_TRACKED_GAMES_MAX
from mystake.pipeline.live_discovery import LiveFixtureDiscovery
from mystake.pipeline.live_odds_dispatcher import (
    LiveOddsDispatcher,
    topic_for_game_id,
)
from mystake.pipeline.notification_processor import NotificationProcessor
from mystake.registry.live_game_registry import LiveGameRegistry
from mystake.sources.cache.client import MystakeCacheClient
from mystake.sources.mqtt.client import ListenerShutdown, MystakeMqttClient
from watch_live_odds import (
    install_shutdown_signal_handlers,
    select_auto_game_ids,
    snapshot_summary,
)

logger = logging.getLogger(__name__)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--observe-seconds",
        type=float,
        default=60.0,
        help="total bounded wall-clock observation window",
    )
    parser.add_argument(
        "--subscribe-after-seconds",
        type=float,
        default=5.0,
        help=(
            "delay before the background thread issues its dynamic "
            "SUBSCRIBE(s), giving the main thread time to reach its "
            "blocking ws.recv()"
        ),
    )
    parser.add_argument(
        "--max-dynamic-games",
        type=int,
        default=2,
        help="how many additional GameIds to dynamically subscribe to",
    )
    return parser


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s - %(message)s",
        datefmt="%H:%M:%S",
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

    needed = min(1 + args.max_dynamic_games, LIVE_TRACKED_GAMES_MAX)

    game_ids = select_auto_game_ids(all_fixtures, max_games=needed)

    if len(game_ids) < 2:
        print("Fewer than 2 real live fixtures available; cannot verify. Aborting.")
        cache_client.close()
        return

    initial_game_id, *dynamic_game_ids = game_ids
    dynamic_game_ids = dynamic_game_ids[: args.max_dynamic_games]

    print(
        f"Initial tracked GameId (subscribed before receive loop starts): {initial_game_id}"
    )
    print(
        f"Dynamic GameId(s) (subscribed from background thread while main "
        f"thread is receiving): {dynamic_game_ids}"
    )

    registry = LiveGameRegistry(
        tracked_game_ids=[initial_game_id, *dynamic_game_ids],
    )
    notification_processor = NotificationProcessor(cache_client=cache_client)
    dispatcher = LiveOddsDispatcher(
        registry=registry,
        notification_processor=notification_processor,
        mqtt_client=mqtt_client,
    )

    stats = {
        game_id: {"notification_count": 0, "initial_snapshot": False}
        for game_id in [initial_game_id, *dynamic_game_ids]
    }

    dynamic_subscribe_timestamps: dict[object, dict[str, float]] = {}

    def dynamic_subscribe_worker() -> None:
        time.sleep(args.subscribe_after_seconds)

        for game_id in dynamic_game_ids:
            if mqtt_client.shutdown_requested:
                return

            topic = topic_for_game_id(game_id)
            request_time = time.monotonic()

            logger.info(
                "BACKGROUND_THREAD dynamically subscribing game_id=%s topic=%s "
                "(main thread should already be blocked in ws.recv() by now)",
                game_id,
                topic,
            )

            try:
                mqtt_client.subscribe(topic)
            except Exception:
                logger.exception(
                    "Dynamic subscribe failed for game_id=%s topic=%s",
                    game_id,
                    topic,
                )
                continue

            completed_time = time.monotonic()
            dynamic_subscribe_timestamps[game_id] = {
                "requested_at": request_time,
                "completed_at": completed_time,
                "total_elapsed": completed_time - request_time,
            }

            logger.info(
                "BACKGROUND_THREAD dynamic subscribe completed game_id=%s "
                "total_elapsed=%.3fs",
                game_id,
                completed_time - request_time,
            )

    try:
        mqtt_client.connect_with_retry()
        logger.info("MQTT connection established")

        initial_topic = topic_for_game_id(initial_game_id)
        mqtt_client.subscribe(initial_topic)
        logger.info(
            "Initial SUBACK accepted game_id=%s topic=%s (before receive loop starts)",
            initial_game_id,
            initial_topic,
        )

        shutdown_timer = threading.Timer(
            args.observe_seconds,
            mqtt_client.request_shutdown,
        )
        shutdown_timer.daemon = True
        shutdown_timer.start()

        subscriber_thread = threading.Thread(
            target=dynamic_subscribe_worker,
            name="dynamic-subscriber",
            daemon=True,
        )
        subscriber_thread.start()

        logger.info(
            "MAIN_THREAD entering receive_publish() loop for up to %.0fs",
            args.observe_seconds,
        )

        while True:
            message = mqtt_client.receive_publish()

            outcome = dispatcher.handle(message)

            if outcome is None:
                continue

            game_stats = stats.get(outcome.game_id)
            if game_stats is None:
                continue

            game_stats["notification_count"] += 1

            if outcome.is_initial:
                game_stats["initial_snapshot"] = True
                logger.info(
                    "game_id=%s INITIAL live snapshot received: %s",
                    outcome.game_id,
                    snapshot_summary(outcome.snapshot, debug=False),
                )
            elif outcome.duplicate:
                logger.info("game_id=%s duplicate notification", outcome.game_id)
            else:
                logger.info(
                    "game_id=%s UPDATE notification received (notification_count=%s)",
                    outcome.game_id,
                    game_stats["notification_count"],
                )

    except (KeyboardInterrupt, ListenerShutdown) as exc:
        logger.info("Stopping (%s)", type(exc).__name__)

    except Exception:
        logger.exception("Stopping due to an unhandled error")
        raise

    finally:
        mqtt_client.close()
        cache_client.close()
        logger.info("Resources closed")

        print()
        print("=" * 80)
        print("DYNAMIC SUBSCRIPTION VERIFICATION REPORT")
        print("=" * 80)
        print(f"Initial GameId (pre-loop): {initial_game_id}")
        print(
            f"  notifications_received: {stats[initial_game_id]['notification_count']}"
        )
        print(
            f"  initial_snapshot_received: {stats[initial_game_id]['initial_snapshot']}"
        )
        print()
        for game_id in dynamic_game_ids:
            timing = dynamic_subscribe_timestamps.get(game_id)
            print(f"Dynamic GameId (subscribed from background thread): {game_id}")
            print(
                f"  subscribe_total_elapsed: {timing['total_elapsed']:.3f}s"
                if timing
                else "  subscribe_total_elapsed: NOT COMPLETED"
            )
            print(f"  notifications_received: {stats[game_id]['notification_count']}")
            print(f"  initial_snapshot_received: {stats[game_id]['initial_snapshot']}")
            print()

        print(
            "See INFO logs above for per-stage monotonic timestamps: "
            "SUBSCRIBE_REQUESTED / SUBSCRIBE_SEND_LOCK_ACQUIRED / "
            "SUBSCRIBE_PACKET_SENT / SUBACK_RECEIVED (became_reader=...) / "
            "SUBSCRIBE_COMPLETED."
        )
        print(
            "became_reader=False on the dynamic SUBSCRIBE's SUBACK_RECEIVED "
            "line is the proof that the main thread already held the "
            "reader role (i.e. was blocked in ws.recv()) at that moment."
        )


if __name__ == "__main__":
    main()
