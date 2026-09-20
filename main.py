import logging
from typing import Any

from mystake.pipeline.live_snapshot_diff import (
    LiveSnapshotDiff,
    diff_live_snapshots,
)
from mystake.pipeline.notification_processor import (
    NotificationProcessor,
)
from mystake.sources.cache.client import MystakeCacheClient
from mystake.sources.mqtt.client import MystakeMqttClient

from mystake.events.mapper import (
    map_live_diff_to_events,
)

TOPIC = "live/gamenew/76321927"


def print_snapshot(
    data: dict[str, Any],
) -> None:
    match = data.get("Match")
    gmk = data.get("gmk")
    mk = data.get("mk")
    timelines = data.get("TimeLines")

    print()
    print("=" * 80)
    print("LIVE SNAPSHOT")
    print("=" * 80)

    print(
        f"GameID    : "
        f"{match.get('GameID') if isinstance(match, dict) else None}"
    )
    print(
        f"Score     : "
        f"{match.get('Score') if isinstance(match, dict) else None}"
    )
    print(
        f"Time      : "
        f"{match.get('MatchTimeExtended') if isinstance(match, dict) else None}"
    )
    print(
        f"BetStatus : "
        f"{match.get('BetStatus') if isinstance(match, dict) else None}"
    )
    print(
        f"gmk       : "
        f"{len(gmk) if isinstance(gmk, list) else None}"
    )
    print(
        f"mk        : "
        f"{len(mk) if isinstance(mk, list) else None}"
    )
    print(
        f"TimeLines : "
        f"{len(timelines) if isinstance(timelines, list) else None}"
    )


def print_diff(
    diff: LiveSnapshotDiff,
) -> None:
    print()
    print("=" * 80)
    print("LIVE SNAPSHOT DIFF")
    print("=" * 80)

    if not diff.has_changes:
        print("No changes.")
        return

    if diff.match_changes:
        print()
        print("MATCH CHANGES")

        for change in diff.match_changes:
            print(
                f"{change.field}: "
                f"{change.old!r} -> {change.new!r}"
            )

    if diff.price_changes:
        print()
        print(
            f"PRICE CHANGES "
            f"({len(diff.price_changes)})"
        )

        for change in diff.price_changes[:30]:
            print(
                f"selection={change.selection_id} "
                f"market={change.market_id} "
                f"{change.old!r} -> {change.new!r}"
            )

        if len(diff.price_changes) > 30:
            print(
                f"... "
                f"{len(diff.price_changes) - 30} "
                f"more price changes"
            )

    if diff.visibility_changes:
        print()
        print(
            f"VISIBILITY CHANGES "
            f"({len(diff.visibility_changes)})"
        )

        for change in diff.visibility_changes:
            print(
                f"selection={change.selection_id} "
                f"market={change.market_id} "
                f"{change.old!r} -> {change.new!r}"
            )

    if diff.added_selections:
        print()
        print(
            f"ADDED SELECTIONS "
            f"({len(diff.added_selections)})"
        )

        for change in diff.added_selections:
            print(
                f"selection={change.selection_id} "
                f"market={change.market_id}"
            )

    if diff.removed_selections:
        print()
        print(
            f"REMOVED SELECTIONS "
            f"({len(diff.removed_selections)})"
        )

        for change in diff.removed_selections:
            print(
                f"selection={change.selection_id} "
                f"market={change.market_id}"
            )

    if diff.new_timeline_items:
        print()
        print(
            f"NEW TIMELINE ITEMS "
            f"({len(diff.new_timeline_items)})"
        )

        for item in diff.new_timeline_items:
            print(item)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s "
            "%(levelname)s "
            "%(name)s - "
            "%(message)s"
        ),
    )

    mqtt_client = MystakeMqttClient()
    cache_client = MystakeCacheClient()

    processor = NotificationProcessor(
        cache_client=cache_client,
    )

    previous_snapshot: dict[str, Any] | None = None
    snapshot_number = 0

    try:
        mqtt_client.connect_with_retry()

        mqtt_client.subscribe(
            TOPIC
        )

        logging.info(
            "Listening continuously for MQTT PUBLISH topic=%s",
            TOPIC,
        )

        while True:
            message = mqtt_client.receive_publish()

            logging.info(
                "MQTT PUBLISH received topic=%s payload=%s",
                message.topic,
                message.payload.decode(
                    "utf-8",
                    errors="replace",
                ),
            )

            result = processor.process(
                message
            )

            if not isinstance(
                result.data,
                dict,
            ):
                logging.warning(
                    "Ignoring non-dict live snapshot type=%s",
                    type(result.data).__name__,
                )
                continue

            snapshot_number += 1

            print()
            print("#" * 80)
            print(
                f"SNAPSHOT #{snapshot_number}"
            )
            print("#" * 80)

            print_snapshot(
                result.data
            )

            if previous_snapshot is None:
                print()
                print(
                    "Initial snapshot stored. "
                    "Waiting for next notification..."
                )
            else:
                diff = diff_live_snapshots(
                    previous_snapshot,
                    result.data,
                )

                print_diff(
                    diff
                )

                events = map_live_diff_to_events(
                    diff,
                    result.data,
                )

                if events:
                    print()
                    print("=" * 80)
                    print("DOMAIN EVENTS")
                    print("=" * 80)

                    for event in events:
                        print()
                        print(event.event_type.value)
                        print(f"game_id : {event.game_id}")

                        for key, value in event.payload.items():
                            print(f"{key:<12}: {value}")

            previous_snapshot = result.data

    except KeyboardInterrupt:
        print()
        logging.info(
            "Stopped by user."
        )

    finally:
        mqtt_client.close()
        cache_client.close()


if __name__ == "__main__":
    main()