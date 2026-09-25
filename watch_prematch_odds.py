"""
Phase 3: bounded prematch market/selection/odds hydration and updates
for a small, explicitly tracked set of GameIds.

Connects to the MyStake MQTT broker, subscribes to `prematch/games`,
hydrates each tracked GameId's authoritative `getprematchgamefull`
snapshot once up front, then revalidates the tracked set (bounded,
never the full discovered fixture catalog) whenever a `prematch/games`
notification arrives, for the lifetime of the process.

Usage:

    uv run python -u watch_prematch_odds.py --game-id 76513163
    uv run python -u watch_prematch_odds.py --game-id 76513163 --game-id 73531572

This does not perform fixture discovery itself - GameIds must already
be known (e.g. from `discover_fixtures.py` / `watch_prematch_header.py`).
"""

import argparse
import logging
import signal

from mystake.config import (
    MQTT_TOPIC_PREMATCH_GAMES,
    PREMATCH_HYDRATION_MAX_CONCURRENCY,
    PREMATCH_HYDRATION_MIN_REQUEST_INTERVAL_SECONDS,
    PREMATCH_TRACKED_GAMES_MAX,
)
from mystake.models.snapshot import Snapshot
from mystake.pipeline.notification_processor import NotificationProcessor
from mystake.pipeline.prematch_games_notification import (
    PrematchGamesRevalidationHandler,
)
from mystake.pipeline.prematch_odds_tracker import PrematchOddsTracker
from mystake.registry.game_snapshot_registry import FetchOutcome, GameSnapshotRegistry
from mystake.sources.cache.client import MystakeCacheClient
from mystake.sources.http.client import MystakeHttpClient
from mystake.sources.mqtt.client import ListenerShutdown, MystakeMqttClient

logger = logging.getLogger(__name__)


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
        required=True,
        help=(
            "GameId to track; may be passed multiple times "
            f"(max {PREMATCH_TRACKED_GAMES_MAX})"
        ),
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=PREMATCH_HYDRATION_MAX_CONCURRENCY,
        help="max simultaneous getprematchgamefull requests",
    )
    parser.add_argument(
        "--min-request-interval",
        type=float,
        default=PREMATCH_HYDRATION_MIN_REQUEST_INTERVAL_SECONDS,
        help="minimum seconds between getprematchgamefull requests",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="log full raw snapshot payloads (never enable with untrusted output sinks)",
    )

    return parser


def describe_snapshot(snapshot: Snapshot, *, debug: bool) -> str:
    market_count = len(snapshot.markets)
    selection_count = sum(len(market.selections) for market in snapshot.markets)

    sample = []

    for market in snapshot.markets:
        for selection in market.selections:
            sample.append(
                f"market={market.id} selection={selection.id} price={selection.price}"
            )

            if len(sample) >= 5:
                break

        if len(sample) >= 5:
            break

    summary = (
        f"game_id={snapshot.game_id} markets={market_count} "
        f"selections={selection_count} sample={sample}"
    )

    if debug:
        summary += f" raw={snapshot.raw}"

    return summary


def log_outcome(
    game_id: int | str,
    outcome: FetchOutcome | None,
    *,
    debug: bool,
) -> None:
    if outcome is None:
        logger.info(
            "game_id=%s revalidation coalesced (fetch already in flight)", game_id
        )
        return

    if outcome.failed:
        logger.warning(
            "game_id=%s refresh failed; previous valid snapshot preserved "
            "(has_previous_snapshot=%s)",
            game_id,
            outcome.snapshot is not None,
        )
        return

    if outcome.is_initial:
        logger.info(
            "game_id=%s initial snapshot received %s",
            game_id,
            describe_snapshot(outcome.snapshot, debug=debug),
        )
        return

    diff = outcome.diff

    if diff is None or not diff.has_changes:
        logger.info("game_id=%s snapshot unchanged", game_id)
        return

    logger.info(
        "game_id=%s snapshot updated added_markets=%s removed_markets=%s "
        "added_selections=%s removed_selections=%s price_changes=%s",
        game_id,
        [c.market_id for c in diff.added_markets],
        [c.market_id for c in diff.removed_markets],
        [c.selection_id for c in diff.added_selections],
        [c.selection_id for c in diff.removed_selections],
        len(diff.price_changes),
    )

    for change in diff.price_changes:
        logger.info(
            "game_id=%s market=%s selection=%s price %s -> %s",
            game_id,
            change.market_id,
            change.selection_id,
            change.old,
            change.new,
        )


def run(
    mqtt_client: MystakeMqttClient,
    tracker: PrematchOddsTracker,
    handler: PrematchGamesRevalidationHandler,
    *,
    debug: bool,
) -> None:
    logger.info(
        "Prematch odds tracker starting tracked_game_ids=%s",
        tracker.registry.tracked_game_ids(),
    )

    mqtt_client.connect_with_retry()
    logger.info("MQTT connection established")

    mqtt_client.subscribe(MQTT_TOPIC_PREMATCH_GAMES)
    logger.info(
        "Subscribed and SUBACK accepted topic=%s",
        MQTT_TOPIC_PREMATCH_GAMES,
    )

    logger.info("Initial tracked-game hydration starting")

    for game_id, outcome in tracker.hydrate_all_tracked().items():
        log_outcome(game_id, outcome, debug=debug)

    logger.info(
        "Listening for prematch/games notifications topic=%s",
        MQTT_TOPIC_PREMATCH_GAMES,
    )

    while True:
        message = mqtt_client.receive_publish()

        if message.topic != MQTT_TOPIC_PREMATCH_GAMES:
            logger.debug("Ignoring PUBLISH on unrelated topic=%s", message.topic)
            continue

        logger.info("prematch/games notification received")

        results = handler.handle(message)

        for game_id, outcome in results.items():
            log_outcome(game_id, outcome, debug=debug)


def serve(
    mqtt_client: MystakeMqttClient,
    tracker: PrematchOddsTracker,
    handler: PrematchGamesRevalidationHandler,
    http_client: MystakeHttpClient,
    cache_client: MystakeCacheClient,
    *,
    debug: bool,
) -> None:
    try:
        run(mqtt_client, tracker, handler, debug=debug)

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


def install_shutdown_signal_handlers(mqtt_client: MystakeMqttClient) -> None:
    def handle_shutdown_signal(signum: int, frame: object) -> None:
        logger.info(
            "Received signal=%s; requesting listener shutdown",
            signal.Signals(signum).name,
        )
        mqtt_client.request_shutdown()

    signal.signal(signal.SIGINT, handle_shutdown_signal)
    signal.signal(signal.SIGTERM, handle_shutdown_signal)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    parser = build_arg_parser()
    args = parser.parse_args()

    game_ids = [parse_game_id(raw) for raw in args.game_ids]

    if len(game_ids) > PREMATCH_TRACKED_GAMES_MAX:
        parser.error(
            f"at most {PREMATCH_TRACKED_GAMES_MAX} tracked GameIds are "
            f"supported, got {len(game_ids)}"
        )

    mqtt_client = MystakeMqttClient()
    http_client = MystakeHttpClient()
    cache_client = MystakeCacheClient()

    install_shutdown_signal_handlers(mqtt_client)

    registry = GameSnapshotRegistry(tracked_game_ids=game_ids)
    tracker = PrematchOddsTracker(
        http_client,
        registry,
        max_concurrency=args.max_concurrency,
        min_request_interval_seconds=args.min_request_interval,
    )
    notification_processor = NotificationProcessor(cache_client=cache_client)
    handler = PrematchGamesRevalidationHandler(
        tracker=tracker,
        notification_processor=notification_processor,
    )

    serve(
        mqtt_client,
        tracker,
        handler,
        http_client,
        cache_client,
        debug=args.debug,
    )


if __name__ == "__main__":
    main()
