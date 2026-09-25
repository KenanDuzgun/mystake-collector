"""
Phase 3C: targeted real odds update verification.

Bounded diagnostic script (not Phase 4) that fixes the Phase 3B gap:
previously tracked GameIds were chosen essentially at random from
discovery data, so real `prematch/games` traffic never happened to
reference any of them. This script instead:

Phase A (observe) - listens to real `prematch/games` notifications and
collects real `UpdateList`/`DeleteList` GameIds, bounded by wall-clock
time and notification count. No hydration happens yet.

Phase B (verify) - after selecting up to `PREMATCH_TRACKED_GAMES_MAX`
of those *actually observed* GameIds (filtered against real
`getheader/en` discovery data for availability) and hydrating their
authoritative `getprematchgamefull` snapshot, continues listening on
the same MQTT connection, bounded by wall-clock time and notification
count, watching for a real notification that references one of the
newly tracked GameIds and produces a real price change.

Usage:

    uv run python -u observe_and_verify_tracked_odds.py

Rate-limiting (AGENTS.md section 4): reuses `PrematchOddsTracker`'s
existing pacing/concurrency bounds unmodified and never fetches more
than the selected tracked set; both phases are bounded by wall-clock
time and notification count rather than unbounded polling.
"""

from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from mystake.config import (
    MQTT_TOPIC_PREMATCH_GAMES,
    PREMATCH_HYDRATION_MAX_CONCURRENCY,
    PREMATCH_HYDRATION_MIN_REQUEST_INTERVAL_SECONDS,
    PREMATCH_TRACKED_GAMES_MAX,
)
from mystake.pipeline.notification_processor import NotificationProcessor
from mystake.pipeline.prematch_discovery import PrematchFixtureDiscovery
from mystake.pipeline.prematch_games_notification import (
    _extract_relevant_game_ids as extract_relevant_game_ids,
)
from mystake.pipeline.prematch_odds_tracker import PrematchOddsTracker
from mystake.registry.game_snapshot_registry import GameSnapshotRegistry
from mystake.sources.cache.client import MystakeCacheClient
from mystake.sources.http.client import MystakeHttpClient
from mystake.sources.mqtt.client import ListenerShutdown, MystakeMqttClient
from watch_prematch_odds import (
    describe_snapshot,
    install_shutdown_signal_handlers,
    log_outcome,
)

logger = logging.getLogger(__name__)

_UNSET = object()


@dataclass
class ObservationResult:
    notifications_seen: int = 0
    observed_game_ids: list[Any] = field(default_factory=list)
    stopped_reason: str = ""
    elapsed_seconds: float = 0.0


@dataclass
class PriceChangeRecord:
    game_id: Any
    market_id: Any
    selection_id: Any
    previous_price: Any
    new_price: Any


@dataclass
class VerificationResult:
    notifications_seen: int = 0
    tracked_match_notifications: int = 0
    fallback_revalidations: int = 0
    price_changes: list[PriceChangeRecord] = field(default_factory=list)
    stopped_reason: str = ""
    elapsed_seconds: float = 0.0


def observe_update_game_ids(
    mqtt_client: MystakeMqttClient,
    notification_processor: NotificationProcessor,
    *,
    max_seconds: float,
    max_notifications: int,
    max_distinct_game_ids: int,
    time_source: Callable[[], float] = time.monotonic,
) -> ObservationResult:
    """
    Phase A: bounded observation of real `prematch/games` traffic.

    Collects GameIds referenced by real `UpdateList`/`DeleteList`
    entries (via the same extraction logic
    `PrematchGamesRevalidationHandler` uses), in first-seen order, with
    no hydration/HTTP calls beyond the cache-URL fetch already required
    to decode each notification.
    """
    result = ObservationResult()
    start = time_source()
    seen: dict[Any, None] = {}

    while True:
        if time_source() - start >= max_seconds:
            result.stopped_reason = "max_seconds_reached"
            break

        if result.notifications_seen >= max_notifications:
            result.stopped_reason = "max_notifications_reached"
            break

        if len(seen) >= max_distinct_game_ids:
            result.stopped_reason = "max_distinct_game_ids_reached"
            break

        message = mqtt_client.receive_publish()

        if message.topic != MQTT_TOPIC_PREMATCH_GAMES:
            logger.debug(
                "Ignoring PUBLISH on unrelated topic=%s during observation",
                message.topic,
            )
            continue

        result.notifications_seen += 1
        logger.info(
            "Real prematch/games notification observed (#%d)",
            result.notifications_seen,
        )

        try:
            value = notification_processor.process(message).data
        except ValueError as exc:
            logger.warning("Could not decode observed notification: %s", exc)
            continue

        game_ids = extract_relevant_game_ids(value)

        if not game_ids:
            logger.info(
                "Observed notification had no extractable UpdateList/DeleteList GameIds"
            )
            continue

        newly_seen = [game_id for game_id in game_ids if game_id not in seen]

        for game_id in game_ids:
            seen.setdefault(game_id, None)

        if newly_seen:
            logger.info(
                "Real UpdateList/DeleteList GameIds observed: %s",
                sorted(newly_seen, key=str),
            )

    result.elapsed_seconds = time_source() - start
    result.observed_game_ids = list(seen.keys())
    return result


def select_available_game_ids(
    observed_game_ids: Iterable[Any],
    is_available: Callable[[Any], bool],
    max_tracked: int,
) -> list[Any]:
    """
    Filters, in first-observed order, to GameIds confirmed present in
    existing discovery data, capped at `max_tracked`.
    """
    selected: list[Any] = []

    for game_id in observed_game_ids:
        if len(selected) >= max_tracked:
            break

        if is_available(game_id):
            selected.append(game_id)

    return selected


def verify_tracked_notifications(
    mqtt_client: MystakeMqttClient,
    tracker: PrematchOddsTracker,
    notification_processor: NotificationProcessor,
    *,
    max_seconds: float,
    max_notifications: int,
    time_source: Callable[[], float] = time.monotonic,
    debug: bool = False,
) -> VerificationResult:
    """
    Phase B: bounded observation of real `prematch/games` traffic for
    the now-tracked GameId set, mirroring
    `PrematchGamesRevalidationHandler.handle`'s dedup/extraction/
    fallback logic but distinguishing, for honest reporting:

    - a real notification whose extracted GameIds actually intersect
      the tracked set (`tracked_match_notifications`), versus
    - inconclusive extraction falling back to a bounded full-tracked-
      set revalidation (`fallback_revalidations`), which is not
      evidence of a real per-GameId match.

    Stops early the moment a real price change is observed.
    """
    result = VerificationResult()
    start = time_source()
    last_value: Any = _UNSET

    while True:
        if time_source() - start >= max_seconds:
            result.stopped_reason = "max_seconds_reached"
            break

        if result.notifications_seen >= max_notifications:
            result.stopped_reason = "max_notifications_reached"
            break

        message = mqtt_client.receive_publish()

        if message.topic != MQTT_TOPIC_PREMATCH_GAMES:
            continue

        result.notifications_seen += 1
        logger.info(
            "Real prematch/games notification received (verify #%d)",
            result.notifications_seen,
        )

        try:
            value = notification_processor.process(message).data
        except ValueError as exc:
            logger.warning(
                "Could not decode verify-phase notification (%s); treating as "
                "inconclusive for extraction, mirroring "
                "PrematchGamesRevalidationHandler",
                exc,
            )
            value = message.payload

        if last_value is not _UNSET and value == last_value:
            logger.info("Duplicate notification; skipping redundant revalidation")
            continue

        last_value = value

        tracked_ids = tracker.registry.tracked_game_ids()
        extracted = extract_relevant_game_ids(value)

        if extracted is None:
            logger.info(
                "GameId extraction inconclusive; falling back to bounded "
                "full tracked-set revalidation (%d ids)",
                len(tracked_ids),
            )
            result.fallback_revalidations += 1
            target_ids: tuple[Any, ...] = tracked_ids
        else:
            target_ids = tuple(
                game_id
                for game_id in tracked_ids
                if game_id in extracted or str(game_id) in extracted
            )

            if not target_ids:
                logger.info(
                    "Notification did not reference any tracked GameId; skipping"
                )
                continue

            logger.info("REAL tracked-GameId notification match: %s", target_ids)
            result.tracked_match_notifications += 1

        outcomes = tracker.hydrate_many(target_ids)

        for game_id, outcome in outcomes.items():
            log_outcome(game_id, outcome, debug=debug)

            if outcome is not None and not outcome.failed and outcome.diff is not None:
                for change in outcome.diff.price_changes:
                    result.price_changes.append(
                        PriceChangeRecord(
                            game_id=game_id,
                            market_id=change.market_id,
                            selection_id=change.selection_id,
                            previous_price=change.old,
                            new_price=change.new,
                        )
                    )

        if result.price_changes:
            result.stopped_reason = "real_price_change_observed"
            break

    result.elapsed_seconds = time_source() - start
    return result


def print_final_report(
    observation: ObservationResult,
    selected: list[Any],
    initial_outcomes: dict[Any, Any] | None,
    verification: VerificationResult | None,
) -> None:
    print()
    print("=" * 70)
    print("PHASE 3C FINAL REPORT")
    print("=" * 70)
    print(
        f"B. Real UpdateList/DeleteList GameIds observed: "
        f"{observation.observed_game_ids}"
    )
    print(
        f"   (notifications_seen={observation.notifications_seen}, "
        f"stopped={observation.stopped_reason})"
    )
    print(f"C. Selected tracked GameIds: {selected}")

    if not selected:
        print(
            "D-H. No real, currently-available tracked GameIds could be "
            "selected from observed traffic - NOT VERIFIED. See logs above."
        )
        return

    if initial_outcomes is not None:
        print("D. Initial authoritative snapshots:")

        for game_id, outcome in initial_outcomes.items():
            if outcome is not None and outcome.snapshot is not None:
                print(f"   {describe_snapshot(outcome.snapshot, debug=False)}")
            else:
                print(f"   game_id={game_id} initial hydration FAILED")

    if verification is None:
        return

    print(
        f"E. Real tracked-GameId notification matches: "
        f"{verification.tracked_match_notifications} "
        f"(fallback_revalidations={verification.fallback_revalidations}, "
        f"notifications_seen={verification.notifications_seen})"
    )
    print(
        "F. Authoritative HTTP revalidation performed: "
        f"{'yes' if verification.tracked_match_notifications or verification.fallback_revalidations else 'no'}"
    )

    if verification.price_changes:
        print("G. REAL PRICE CHANGES OBSERVED:")

        for change in verification.price_changes:
            print(
                f"   GameId={change.game_id} MarketId={change.market_id} "
                f"SelectionId={change.selection_id} "
                f"PreviousPrice={change.previous_price} "
                f"NewPrice={change.new_price}"
            )
    else:
        print(
            "G. REAL ODDS CHANGE: NOT VERIFIED "
            "(no real price change observed in the bounded window)"
        )

    print(
        f"H. Verify-phase stop reason: {verification.stopped_reason} "
        f"(elapsed={verification.elapsed_seconds:.1f}s)"
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--observe-seconds",
        type=float,
        default=180.0,
        help="max wall-clock seconds to observe real traffic for candidate GameIds",
    )
    parser.add_argument(
        "--observe-max-notifications",
        type=int,
        default=100,
        help="max notifications to observe before selecting tracked GameIds",
    )
    parser.add_argument(
        "--max-tracked",
        type=int,
        default=PREMATCH_TRACKED_GAMES_MAX,
        help=f"max tracked GameIds (hard cap {PREMATCH_TRACKED_GAMES_MAX})",
    )
    parser.add_argument(
        "--verify-seconds",
        type=float,
        default=240.0,
        help="max wall-clock seconds to observe traffic for a tracked-GameId match",
    )
    parser.add_argument(
        "--verify-max-notifications",
        type=int,
        default=200,
        help="max notifications to observe during verification",
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


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    parser = build_arg_parser()
    args = parser.parse_args()

    if args.max_tracked > PREMATCH_TRACKED_GAMES_MAX:
        parser.error(f"--max-tracked cannot exceed {PREMATCH_TRACKED_GAMES_MAX}")

    mqtt_client = MystakeMqttClient()
    http_client = MystakeHttpClient()
    cache_client = MystakeCacheClient()

    install_shutdown_signal_handlers(mqtt_client)

    notification_processor = NotificationProcessor(cache_client=cache_client)
    prematch_discovery = PrematchFixtureDiscovery(http_client=http_client)

    observation = ObservationResult()
    selected: list[Any] = []
    initial_outcomes = None
    verification = None

    try:
        mqtt_client.connect_with_retry()
        logger.info("MQTT connection established")

        mqtt_client.subscribe(MQTT_TOPIC_PREMATCH_GAMES)
        logger.info(
            "Subscribed and SUBACK accepted topic=%s", MQTT_TOPIC_PREMATCH_GAMES
        )

        logger.info(
            "Running real getheader/en fixture discovery for availability checks"
        )
        discovery_diff = prematch_discovery.refresh()

        if discovery_diff is None:
            logger.error(
                "Fixture discovery failed; cannot verify GameId availability. Aborting."
            )
            return

        logger.info(
            "Fixture discovery complete: %d fixtures known",
            len(prematch_discovery.registry.list_all()),
        )

        logger.info(
            "PHASE A: observing real prematch/games notifications "
            "(max_seconds=%.0f max_notifications=%d) for up to %d real "
            "UpdateList/DeleteList GameIds",
            args.observe_seconds,
            args.observe_max_notifications,
            args.max_tracked,
        )

        observation = observe_update_game_ids(
            mqtt_client,
            notification_processor,
            max_seconds=args.observe_seconds,
            max_notifications=args.observe_max_notifications,
            max_distinct_game_ids=args.max_tracked,
        )

        logger.info(
            "PHASE A complete (%s): notifications_seen=%d "
            "distinct_game_ids_observed=%s",
            observation.stopped_reason,
            observation.notifications_seen,
            observation.observed_game_ids,
        )

        selected = select_available_game_ids(
            observation.observed_game_ids,
            is_available=lambda game_id: (
                prematch_discovery.registry.get(game_id) is not None
            ),
            max_tracked=args.max_tracked,
        )

        logger.info(
            "Selected tracked GameIds (observed in real traffic AND present "
            "in discovery data): %s",
            selected,
        )

        if not selected:
            logger.warning(
                "No observed GameId was confirmed available in discovery "
                "data; nothing to hydrate or verify."
            )
            return

        registry = GameSnapshotRegistry(tracked_game_ids=selected)
        tracker = PrematchOddsTracker(
            http_client,
            registry,
            max_concurrency=args.max_concurrency,
            min_request_interval_seconds=args.min_request_interval,
        )

        logger.info(
            "Hydrating authoritative getprematchgamefull snapshots for "
            "selected tracked GameIds"
        )

        initial_outcomes = tracker.hydrate_all_tracked()

        for game_id, outcome in initial_outcomes.items():
            log_outcome(game_id, outcome, debug=args.debug)

        logger.info(
            "PHASE B: continuing to observe real prematch/games notifications "
            "(max_seconds=%.0f max_notifications=%d), watching for a real "
            "tracked-GameId match",
            args.verify_seconds,
            args.verify_max_notifications,
        )

        verification = verify_tracked_notifications(
            mqtt_client,
            tracker,
            notification_processor,
            max_seconds=args.verify_seconds,
            max_notifications=args.verify_max_notifications,
            debug=args.debug,
        )

        logger.info("PHASE B complete (%s)", verification.stopped_reason)

    except (KeyboardInterrupt, ListenerShutdown) as exc:
        logger.info("Stopping (%s)", type(exc).__name__)

    except Exception:
        logger.exception("Stopping due to an unhandled error")
        raise

    finally:
        mqtt_client.close()
        http_client.close()
        cache_client.close()
        logger.info("Resources closed")

        print_final_report(observation, selected, initial_outcomes, verification)


if __name__ == "__main__":
    main()
