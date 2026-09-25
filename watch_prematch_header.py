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
import signal

from mystake.config import MQTT_TOPIC_PREMATCH_HEADER
from mystake.pipeline.notification_processor import NotificationProcessor
from mystake.pipeline.prematch_discovery import (
    PrematchFixtureDiscovery,
    PrematchHeaderRefreshHandler,
)
from mystake.registry.fixture_registry import FixtureRegistryDiff
from mystake.sources.cache.client import MystakeCacheClient
from mystake.sources.http.client import MystakeHttpClient
from mystake.sources.mqtt.client import ListenerShutdown, MystakeMqttClient

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


def serve(
    mqtt_client: MystakeMqttClient,
    handler: PrematchHeaderRefreshHandler,
    http_client: MystakeHttpClient,
    cache_client: MystakeCacheClient,
) -> None:
    """
    Run the listener and guarantee resource cleanup on shutdown,
    whether that shutdown is a Ctrl+C, or an unhandled error (e.g. a
    rejected subscription) that must not be silently swallowed.
    """
    try:
        run(mqtt_client, handler)

    except (KeyboardInterrupt, ListenerShutdown) as exc:
        logger.info(
            "Listener stopping (%s)",
            type(exc).__name__,
        )

    except Exception:
        logger.exception("Listener stopping due to an unhandled error")
        raise

    finally:
        mqtt_client.close()
        http_client.close()
        cache_client.close()
        logger.info("Listener stopped; resources closed")


def install_shutdown_signal_handlers(
    mqtt_client: MystakeMqttClient,
) -> None:
    """
    SIGINT and SIGTERM both request a graceful shutdown by calling
    `mqtt_client.request_shutdown()` only - the handler itself does not
    raise.

    `request_shutdown()` sets a flag and force-closes the raw MQTT
    socket; `MystakeMqttClient` then surfaces `ListenerShutdown` itself
    from a controlled checkpoint (top of `receive_publish`, or its own
    `except ConnectionError` handling once the forced socket closure is
    observed). Raising directly from the signal handler was deliberately
    avoided: if the signal lands while a notification-triggered
    `getheader/en` refresh is in flight, an exception raised there would
    be caught and swallowed by `PrematchFixtureDiscovery.refresh()`'s
    broad `except Exception` (by design, to preserve registry state on
    HTTP/parse failures - see AGENTS.md section 5), silently discarding
    the shutdown request instead of stopping the listener.

    Python's default SIGINT handling (raise KeyboardInterrupt) is
    replaced so `request_shutdown()` runs on Ctrl+C too. SIGTERM
    previously had no handler at all, so it terminated the process
    immediately without running any cleanup.
    """

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

    mqtt_client = MystakeMqttClient()
    http_client = MystakeHttpClient()
    cache_client = MystakeCacheClient()

    install_shutdown_signal_handlers(mqtt_client)

    discovery = PrematchFixtureDiscovery(http_client=http_client)
    notification_processor = NotificationProcessor(cache_client=cache_client)
    handler = PrematchHeaderRefreshHandler(
        discovery=discovery,
        notification_processor=notification_processor,
    )

    serve(mqtt_client, handler, http_client, cache_client)


if __name__ == "__main__":
    main()
