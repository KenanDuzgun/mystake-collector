from __future__ import annotations

import logging

from mystake.config import LIVE_HEADER_CACHE_KEY
from mystake.pipeline.cache_decoder import decode_cache_response
from mystake.pipeline.live_header_parser import parse_live_header
from mystake.registry.fixture_registry import (
    FixtureRegistry,
    FixtureRegistryDiff,
)
from mystake.sources.cache.client import (
    MystakeCacheClient,
    build_cache_get_url,
)

logger = logging.getLogger(__name__)


class LiveFixtureDiscovery:
    """
    Fetches `live/headernew/en` via the cache-indirection pipeline,
    parses it, and refreshes a `FixtureRegistry`.

    No MQTT notification mechanism is used for the live header (none
    has been observed) - callers are expected to invoke `refresh()`
    explicitly, e.g. on startup and on whatever cadence the caller
    decides.

    Accuracy rule (AGENTS.md section 5): if the HTTP request fails or
    the response cannot be decoded/parsed, the previous registry state
    is preserved untouched and `refresh()` returns `None`.
    """

    def __init__(
        self,
        cache_client: MystakeCacheClient,
        registry: FixtureRegistry | None = None,
    ) -> None:
        self.cache_client = cache_client
        self.registry = registry if registry is not None else FixtureRegistry()

    def refresh(self) -> FixtureRegistryDiff | None:
        url = build_cache_get_url(LIVE_HEADER_CACHE_KEY)

        try:
            raw_content = self.cache_client.get(url)
            payload = decode_cache_response(raw_content)
        except Exception:
            logger.exception(
                "live/headernew/en fetch/decode failed; "
                "preserving previous live registry"
            )
            return None

        try:
            fixtures = parse_live_header(payload)
        except Exception:
            logger.exception(
                "live/headernew/en response could not be parsed; "
                "preserving previous live registry"
            )
            return None

        diff = self.registry.refresh(fixtures)

        logger.info(
            "Live registry refreshed "
            "added=%s removed=%s metadata_changed=%s unchanged=%s",
            len(diff.added),
            len(diff.removed),
            len(diff.metadata_changed),
            len(diff.unchanged),
        )

        return diff
