from __future__ import annotations

import logging
from typing import Any

from mystake.config import PREMATCH_GETHEADER_URL
from mystake.pipeline.notification_processor import NotificationProcessor
from mystake.pipeline.prematch_header_parser import parse_prematch_header
from mystake.registry.fixture_registry import (
    FixtureRegistry,
    FixtureRegistryDiff,
)
from mystake.sources.http.client import MystakeHttpClient
from mystake.sources.mqtt.message import MqttPublishMessage

logger = logging.getLogger(__name__)

_UNSET = object()


class PrematchFixtureDiscovery:
    """
    Fetches `getheader/en`, parses the fixture hierarchy, and refreshes
    a `FixtureRegistry`.

    Accuracy rule (AGENTS.md section 5): if the HTTP request fails or
    the response cannot be decoded/parsed, the previous registry state
    is preserved untouched and `refresh()` returns `None` - it is
    never partially applied.
    """

    def __init__(
        self,
        http_client: MystakeHttpClient,
        registry: FixtureRegistry | None = None,
    ) -> None:
        self.http_client = http_client
        self.registry = registry if registry is not None else FixtureRegistry()

    def refresh(self) -> FixtureRegistryDiff | None:
        try:
            payload = self.http_client.get_json(PREMATCH_GETHEADER_URL)
        except Exception:
            logger.exception(
                "getheader/en request failed; preserving previous prematch registry"
            )
            return None

        try:
            fixtures = parse_prematch_header(payload)
        except Exception:
            logger.exception(
                "getheader/en response could not be parsed; "
                "preserving previous prematch registry"
            )
            return None

        diff = self.registry.refresh(fixtures)

        logger.info(
            "Prematch registry refreshed "
            "added=%s removed=%s metadata_changed=%s unchanged=%s",
            len(diff.added),
            len(diff.removed),
            len(diff.metadata_changed),
            len(diff.unchanged),
        )

        return diff


class PrematchHeaderRefreshHandler:
    """
    Handles MQTT `prematch/header` notifications by triggering a
    `PrematchFixtureDiscovery.refresh()`.

    Consecutive notifications carrying an identical decoded value (or,
    if the payload is not cache-indirected, identical raw bytes) are
    treated as duplicates and skipped, per the "avoid unnecessary
    duplicate refreshes" / "do not assume every notification means an
    actual fixture change" guidance. This is a conservative,
    best-effort dedup - it does not assume the exact semantics of the
    `prematch/header` payload, which are UNKNOWN.
    """

    def __init__(
        self,
        discovery: PrematchFixtureDiscovery,
        notification_processor: NotificationProcessor | None = None,
    ) -> None:
        self.discovery = discovery
        self.notification_processor = notification_processor
        self._last_notification_value: Any = _UNSET

    def handle(
        self,
        message: MqttPublishMessage,
    ) -> FixtureRegistryDiff | None:
        value = self._decode_notification_value(message)

        if (
            value is not _UNSET
            and self._last_notification_value is not _UNSET
            and value == self._last_notification_value
        ):
            logger.info(
                "Duplicate prematch/header notification; "
                "skipping redundant getheader refresh"
            )
            return None

        self._last_notification_value = value

        return self.discovery.refresh()

    def _decode_notification_value(
        self,
        message: MqttPublishMessage,
    ) -> Any:
        if self.notification_processor is not None:
            try:
                return self.notification_processor.process(message).data
            except ValueError:
                pass

        return message.payload
