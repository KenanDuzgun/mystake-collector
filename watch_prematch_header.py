"""
Phase 2B: continuous prematch fixture registry refresh driven by MQTT
`prematch/header` notifications.

Connects to the MyStake MQTT broker, subscribes to `prematch/header`,
performs an initial `getheader/en` discovery to populate the prematch
`FixtureRegistry`, then dispatches every subsequent notification to the
existing `PrematchHeaderRefreshHandler` for the lifetime of the process.

Usage:

    uv run python watch_prematch_header.py

This does not fetch `getprematchgamefull` or hydrate markets/odds for
any fixture - prematch fixture discovery refresh only (Phase 2B scope).
"""

import logging

from mystake.config import MQTT_TOPIC_PREMATCH_HEADER
from mystake.pipeline.notification_processor import NotificationProcessor
from mystake.pipeline.prematch_discovery import (
    PrematchFixtureDiscovery,
    PrematchHeaderRefreshHandler,
)
from mystake.registry.fixture_registry import FixtureRegistryDiff
from mystake.sources.cache.client import MystakeCacheClient
from mystake.sources.http.client import MystakeHttpClient
from mystake.sources.mqtt.client import MystakeMqttClient

logger = logging.getLogger(__name__)


def log_reconciliation(diff: FixtureRegistryDiff | None, *, context: str) -> None:
    if diff is None:
        logger.warning(
            "%s produced no diff (deduplicated, or refresh failed and the "
            "previous registry was preserved - see preceding log entries)",
            context,
        )
        return

    total = (
        len(diff.added)
        + len(diff.removed)
        + len(diff.metadata_changed)
        + len(diff.unchanged)
    )

    logger.info(
        "%s reconciled added=%s removed=%s changed=%s unchanged=%s total=%s",
        context,
        len(diff.added),
        len(diff.removed),
        len(diff.metadata_changed),
        len(diff.unchanged),
        total,
    )


def run(
    mqtt_client: MystakeMqttClient,
    handler: PrematchHeaderRefreshHandler,
) -> None:
    """
    Establish the MQTT subscription, perform initial discovery, then
    process `prematch/header` notifications until interrupted.

    Subscribing before running the initial HTTP discovery means a
    notification that arrives during bootstrap is never silently
    dropped: `MystakeMqttClient.subscribe` blocks until SUBACK is
    confirmed, queuing (not discarding) any PUBLISH observed while
    waiting for it, and any PUBLISH that arrives afterwards - including
    while the initial `getheader/en` request is in flight - is buffered
    by the underlying (blocking) WebSocket/OS socket until this loop
    calls `receive_publish`. Because this runs on a single thread, the
    initial discovery always completes strictly before any
    notification-triggered refresh starts, so a later refresh can never
    be overwritten by the initial snapshot.
    """
    logger.info("Prematch header listener starting")

    mqtt_client.connect_with_retry()
    logger.info("MQTT connection established")

    mqtt_client.subscribe(MQTT_TOPIC_PREMATCH_HEADER)
    logger.info(
        "Subscribed and SUBACK accepted topic=%s",
        MQTT_TOPIC_PREMATCH_HEADER,
    )

    logger.info("Initial prematch discovery starting")

    initial_diff = handler.discovery.refresh()

    log_reconciliation(initial_diff, context="Initial discovery")
    logger.info(
        "Initial fixture count=%s",
        len(handler.discovery.registry.list_all()),
    )

    logger.info(
        "Listening for prematch/header notifications topic=%s",
        MQTT_TOPIC_PREMATCH_HEADER,
    )

    while True:
        message = mqtt_client.receive_publish()

        if message.topic != MQTT_TOPIC_PREMATCH_HEADER:
            logger.debug(
                "Ignoring PUBLISH on unrelated topic=%s",
                message.topic,
            )
            continue

        logger.info("prematch/header notification received")

        diff = handler.handle(message)

        log_reconciliation(diff, context="Header refresh")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    mqtt_client = MystakeMqttClient()
    http_client = MystakeHttpClient()
    cache_client = MystakeCacheClient()

    discovery = PrematchFixtureDiscovery(http_client=http_client)
    notification_processor = NotificationProcessor(cache_client=cache_client)
    handler = PrematchHeaderRefreshHandler(
        discovery=discovery,
        notification_processor=notification_processor,
    )

    try:
        run(mqtt_client, handler)

    except KeyboardInterrupt:
        logger.info("Listener stopping (KeyboardInterrupt)")

    except Exception:
        logger.exception("Listener stopping due to an unhandled error")
        raise

    finally:
        mqtt_client.close()
        http_client.close()
        cache_client.close()
        logger.info("Listener stopped; resources closed")


if __name__ == "__main__":
    main()
