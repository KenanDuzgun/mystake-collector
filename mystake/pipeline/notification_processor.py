from dataclasses import dataclass
from typing import Any

from mystake.pipeline.cache_decoder import decode_cache_response
from mystake.sources.cache.client import MystakeCacheClient
from mystake.sources.mqtt.message import MqttPublishMessage


CACHE_PREFIX = b"cache:"


@dataclass(frozen=True)
class ProcessedNotification:
    topic: str
    cache_url: str
    data: Any


class NotificationProcessor:
    def __init__(
        self,
        cache_client: MystakeCacheClient,
    ) -> None:
        self.cache_client = cache_client

    def process(
        self,
        message: MqttPublishMessage,
    ) -> ProcessedNotification:
        cache_url = self._extract_cache_url(
            message.payload
        )

        raw_content = self.cache_client.get(
            cache_url
        )

        decoded = decode_cache_response(
            raw_content
        )

        return ProcessedNotification(
            topic=message.topic,
            cache_url=cache_url,
            data=decoded,
        )

    @staticmethod
    def _extract_cache_url(
        payload: bytes,
    ) -> str:
        if not payload.startswith(
            CACHE_PREFIX
        ):
            raise ValueError(
                "MQTT payload is not a cache notification"
            )

        raw_url = payload[
            len(CACHE_PREFIX):
        ]

        try:
            return raw_url.decode(
                "utf-8"
            )
        except UnicodeDecodeError as exc:
            raise ValueError(
                "Cache URL is not valid UTF-8"
            ) from exc