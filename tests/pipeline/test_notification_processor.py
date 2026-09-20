from mystake.pipeline.notification_processor import (
    NotificationProcessor,
)
from mystake.sources.mqtt.message import (
    MqttPublishMessage,
)


class FakeCacheClient:
    def get(
        self,
        url: str,
    ) -> bytes:
        assert url == (
            "https://example.com/cache"
        )

        return (
            b'"eyJVcGRhdGVMaXN0IjpbXSwiRGVsZXRlTGlzdCI6W119"'
        )


def test_process_cache_notification() -> None:
    processor = NotificationProcessor(
        cache_client=FakeCacheClient(),
    )

    message = MqttPublishMessage(
        topic="prematch/games",
        payload=(
            b"cache:https://example.com/cache"
        ),
        qos=0,
        retain=False,
        dup=False,
        packet_id=None,
    )

    result = processor.process(
        message
    )

    assert result.topic == "prematch/games"

    assert result.cache_url == (
        "https://example.com/cache"
    )

    assert result.data == {
        "UpdateList": [],
        "DeleteList": [],
    }