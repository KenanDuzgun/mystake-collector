from mystake.pipeline.notification_processor import NotificationProcessor
from mystake.pipeline.prematch_discovery import (
    PrematchFixtureDiscovery,
    PrematchHeaderRefreshHandler,
)
from mystake.sources.mqtt.message import MqttPublishMessage


class FakeHttpClient:
    def __init__(self, responses):
        self._responses = list(responses)

    def get_json(self, url: str):
        response = self._responses.pop(0)

        if isinstance(response, Exception):
            raise response

        return response


HEADER_PAYLOAD = {
    "Sports": [
        {
            "ID": 1,
            "Name": "Soccer",
            "Regions": [
                {
                    "ID": 10,
                    "Name": "England",
                    "Champs": [
                        {
                            "ID": 100,
                            "Name": "Premier League",
                            "GameSmallItems": [
                                {"ID": 1, "StartTime": 1},
                            ],
                        },
                    ],
                },
            ],
        },
    ],
}


def test_refresh_discovers_fixtures():
    discovery = PrematchFixtureDiscovery(
        http_client=FakeHttpClient([HEADER_PAYLOAD]),
    )

    diff = discovery.refresh()

    assert diff is not None
    assert len(diff.added) == 1
    assert discovery.registry.get(1) is not None


def test_http_failure_preserves_previous_registry():
    discovery = PrematchFixtureDiscovery(
        http_client=FakeHttpClient([HEADER_PAYLOAD, RuntimeError("boom")]),
    )

    discovery.refresh()
    previous_fixtures = discovery.registry.list_all()

    diff = discovery.refresh()

    assert diff is None
    assert discovery.registry.list_all() == previous_fixtures


def test_malformed_response_preserves_previous_registry():
    discovery = PrematchFixtureDiscovery(
        http_client=FakeHttpClient([HEADER_PAYLOAD, {"unexpected": "shape"}]),
    )

    discovery.refresh()
    previous_fixtures = discovery.registry.list_all()

    # A response with no locatable 'Sports' list -> parse_prematch_header
    # raises, rather than being silently treated as zero fixtures.
    diff = discovery.refresh()

    assert diff is None
    assert discovery.registry.list_all() == previous_fixtures


def test_successful_refresh_with_no_fixture_changes():
    discovery = PrematchFixtureDiscovery(
        http_client=FakeHttpClient([HEADER_PAYLOAD, HEADER_PAYLOAD]),
    )

    discovery.refresh()
    diff = discovery.refresh()

    assert diff is not None
    assert diff.has_fixture_changes is False
    assert len(diff.unchanged) == 1


def make_message(payload: bytes) -> MqttPublishMessage:
    return MqttPublishMessage(
        topic="prematch/header",
        payload=payload,
        qos=0,
        retain=False,
        dup=False,
        packet_id=None,
    )


def test_mqtt_header_notification_triggers_refresh():
    discovery = PrematchFixtureDiscovery(
        http_client=FakeHttpClient([HEADER_PAYLOAD]),
    )
    handler = PrematchHeaderRefreshHandler(discovery=discovery)

    diff = handler.handle(make_message(b"trigger-1"))

    assert diff is not None
    assert len(diff.added) == 1


def test_duplicate_mqtt_notifications_skip_redundant_refresh():
    discovery = PrematchFixtureDiscovery(
        http_client=FakeHttpClient([HEADER_PAYLOAD, HEADER_PAYLOAD]),
    )
    handler = PrematchHeaderRefreshHandler(discovery=discovery)

    first = handler.handle(make_message(b"same-payload"))
    second = handler.handle(make_message(b"same-payload"))

    assert first is not None
    assert second is None


def test_distinct_mqtt_notifications_each_trigger_refresh():
    discovery = PrematchFixtureDiscovery(
        http_client=FakeHttpClient([HEADER_PAYLOAD, HEADER_PAYLOAD]),
    )
    handler = PrematchHeaderRefreshHandler(discovery=discovery)

    first = handler.handle(make_message(b"payload-a"))
    second = handler.handle(make_message(b"payload-b"))

    assert first is not None
    assert second is not None


class FakeCacheClient:
    def get(self, url: str) -> bytes:
        raise AssertionError("cache client should not be used for non-cache payloads")


def test_handler_uses_notification_processor_when_provided():
    discovery = PrematchFixtureDiscovery(
        http_client=FakeHttpClient([HEADER_PAYLOAD, HEADER_PAYLOAD]),
    )
    processor = NotificationProcessor(cache_client=FakeCacheClient())
    handler = PrematchHeaderRefreshHandler(
        discovery=discovery,
        notification_processor=processor,
    )

    # Not a `cache:`-prefixed payload -> NotificationProcessor.process
    # raises ValueError, and the handler falls back to raw bytes for
    # dedup instead of propagating the error.
    diff = handler.handle(make_message(b"not-a-cache-url"))

    assert diff is not None
