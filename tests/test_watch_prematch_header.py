import signal
from unittest.mock import Mock

import pytest

from mystake.config import MQTT_TOPIC_PREMATCH_HEADER
from mystake.pipeline.notification_processor import NotificationProcessor
from mystake.pipeline.prematch_discovery import (
    PrematchFixtureDiscovery,
    PrematchHeaderRefreshHandler,
)
from mystake.sources.mqtt.client import ListenerShutdown, MystakeMqttClient
from mystake.sources.mqtt.message import MqttPublishMessage
from watch_prematch_header import install_shutdown_signal_handlers, run, serve


class StopTest(Exception):
    """Raised by test doubles to end an otherwise-infinite `run()` loop."""


class FakeHttpClient:
    def __init__(self, responses):
        self._responses = list(responses)

    def get_json(self, url: str):
        if not self._responses:
            raise AssertionError("no more getheader/en responses queued")

        response = self._responses.pop(0)

        if isinstance(response, Exception):
            raise response

        return response


class FakeMqttClient:
    def __init__(self, messages, *, subscribe_error: Exception | None = None):
        self._messages = list(messages)
        self._subscribe_error = subscribe_error
        self.connect_calls = 0
        self.subscribed_topics: list[str] = []
        self.closed = False

    def connect_with_retry(self) -> None:
        self.connect_calls += 1

    def subscribe(self, topic: str, qos: int = 0) -> int:
        if self._subscribe_error is not None:
            raise self._subscribe_error

        self.subscribed_topics.append(topic)
        return 1

    def receive_publish(self) -> MqttPublishMessage:
        if not self._messages:
            raise StopTest

        item = self._messages.pop(0)

        if isinstance(item, BaseException):
            raise item

        return item

    def close(self) -> None:
        self.closed = True


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

HEADER_PAYLOAD_WITH_ADDED_FIXTURE = {
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
                                {"ID": 2, "StartTime": 2},
                            ],
                        },
                    ],
                },
            ],
        },
    ],
}


def make_message(payload: bytes, topic: str = MQTT_TOPIC_PREMATCH_HEADER):
    return MqttPublishMessage(
        topic=topic,
        payload=payload,
        qos=0,
        retain=False,
        dup=False,
        packet_id=None,
    )


def make_handler(*responses):
    discovery = PrematchFixtureDiscovery(http_client=FakeHttpClient(list(responses)))
    handler = PrematchHeaderRefreshHandler(discovery=discovery)
    return discovery, handler


def test_initial_discovery_connects_subscribes_and_populates_registry():
    discovery, handler = make_handler(HEADER_PAYLOAD)
    mqtt_client = FakeMqttClient([])

    with pytest.raises(StopTest):
        run(mqtt_client, handler)

    assert mqtt_client.connect_calls == 1
    assert mqtt_client.subscribed_topics == [MQTT_TOPIC_PREMATCH_HEADER]
    assert discovery.registry.get(1) is not None


def test_subscription_happens_before_initial_discovery():
    order: list[str] = []

    discovery, handler = make_handler(HEADER_PAYLOAD)

    original_refresh = discovery.refresh

    def tracked_refresh():
        order.append("discovery")
        return original_refresh()

    discovery.refresh = tracked_refresh

    class OrderedMqttClient(FakeMqttClient):
        def subscribe(self, topic: str, qos: int = 0) -> int:
            order.append("subscribe")
            return super().subscribe(topic, qos)

    mqtt_client = OrderedMqttClient([])

    with pytest.raises(StopTest):
        run(mqtt_client, handler)

    assert order == ["subscribe", "discovery"]


def test_publish_dispatches_to_handler_and_reconciles_added_fixture():
    discovery, handler = make_handler(HEADER_PAYLOAD, HEADER_PAYLOAD_WITH_ADDED_FIXTURE)
    mqtt_client = FakeMqttClient([make_message(b"trigger-1")])

    with pytest.raises(StopTest):
        run(mqtt_client, handler)

    assert discovery.registry.get(1) is not None
    assert discovery.registry.get(2) is not None


def test_no_change_notification_reports_unchanged_fixtures():
    discovery, handler = make_handler(HEADER_PAYLOAD, HEADER_PAYLOAD)
    mqtt_client = FakeMqttClient([make_message(b"trigger-1")])

    with pytest.raises(StopTest):
        run(mqtt_client, handler)

    assert len(discovery.registry.list_all()) == 1


def test_duplicate_notification_is_coalesced_and_skips_http_refresh():
    # Only 2 responses queued: one for the initial discovery, one for
    # the first (non-duplicate) notification. If the duplicate second
    # notification incorrectly triggered another HTTP refresh,
    # FakeHttpClient would raise on an empty queue and `run` would
    # propagate that error instead of the expected StopTest.
    _discovery, handler = make_handler(HEADER_PAYLOAD, HEADER_PAYLOAD)
    mqtt_client = FakeMqttClient(
        [make_message(b"same-payload"), make_message(b"same-payload")]
    )

    with pytest.raises(StopTest):
        run(mqtt_client, handler)


def test_distinct_notifications_are_not_lost_during_active_processing():
    discovery, handler = make_handler(
        HEADER_PAYLOAD,
        HEADER_PAYLOAD,
        HEADER_PAYLOAD_WITH_ADDED_FIXTURE,
    )
    mqtt_client = FakeMqttClient(
        [make_message(b"payload-a"), make_message(b"payload-b")]
    )

    with pytest.raises(StopTest):
        run(mqtt_client, handler)

    assert discovery.registry.get(2) is not None


def test_transient_http_failure_preserves_previous_registry():
    discovery, handler = make_handler(HEADER_PAYLOAD, RuntimeError("boom"))
    mqtt_client = FakeMqttClient([make_message(b"trigger-1")])

    with pytest.raises(StopTest):
        run(mqtt_client, handler)

    assert discovery.registry.get(1) is not None
    assert len(discovery.registry.list_all()) == 1


def test_malformed_header_response_preserves_previous_registry():
    discovery, handler = make_handler(HEADER_PAYLOAD, {"unexpected": "shape"})
    mqtt_client = FakeMqttClient([make_message(b"trigger-1")])

    with pytest.raises(StopTest):
        run(mqtt_client, handler)

    assert discovery.registry.get(1) is not None
    assert len(discovery.registry.list_all()) == 1


def test_non_prematch_header_publish_is_ignored():
    discovery, handler = make_handler(HEADER_PAYLOAD)
    mqtt_client = FakeMqttClient(
        [make_message(b"cache:whatever", topic="live/gamenew/123")]
    )

    with pytest.raises(StopTest):
        run(mqtt_client, handler)

    # Only the initial discovery's fixture is present; the unrelated
    # PUBLISH must not have triggered a second getheader/en fetch
    # (FakeHttpClient only had one response queued).
    assert len(discovery.registry.list_all()) == 1


def test_subscription_rejection_propagates_and_is_not_swallowed():
    _discovery, handler = make_handler(HEADER_PAYLOAD)
    mqtt_client = FakeMqttClient(
        [],
        subscribe_error=RuntimeError("MQTT subscription rejected or unexpected SUBACK"),
    )

    with pytest.raises(RuntimeError, match="rejected"):
        run(mqtt_client, handler)


def test_reconnect_and_resubscribe_then_still_processes_notification(monkeypatch):
    """
    Uses the real `MystakeMqttClient` (its own reconnect/resubscribe
    unit tests already cover the protocol-level details) to verify the
    executable's `run` loop survives a transient `ConnectionError` and
    keeps dispatching subsequent notifications to the refresh handler.
    """
    client = MystakeMqttClient()

    discovery, handler = make_handler(HEADER_PAYLOAD, HEADER_PAYLOAD)

    monkeypatch.setattr(client, "connect_with_retry", lambda: None)

    def fake_subscribe(topic: str, qos: int = 0) -> int:
        client._subscriptions[topic] = qos
        return 1

    monkeypatch.setattr(client, "subscribe", fake_subscribe)

    reconnect_calls = []

    def fake_reconnect() -> None:
        reconnect_calls.append(1)

    monkeypatch.setattr(client, "_reconnect_and_resubscribe", fake_reconnect)

    publish_packet = b"\x30\x18\x00\x0fprematch/headertrigger"

    calls = 0

    def fake_receive_raw() -> bytes:
        nonlocal calls
        calls += 1

        if calls == 1:
            raise ConnectionError("simulated disconnect")

        if calls == 2:
            return publish_packet

        raise StopTest

    monkeypatch.setattr(client, "receive_raw", fake_receive_raw)

    with pytest.raises(StopTest):
        run(client, handler)

    assert reconnect_calls == [1]
    assert len(discovery.registry.list_all()) == 1


class FakeHttpClientWithClose(FakeHttpClient):
    def __init__(self, responses):
        super().__init__(responses)
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeCacheClient:
    def __init__(self):
        self.closed = False

    def get(self, url: str) -> bytes:
        raise AssertionError("cache client should not be used in this test")

    def close(self) -> None:
        self.closed = True


def test_serve_closes_resources_on_keyboard_interrupt():
    _discovery, handler = make_handler(HEADER_PAYLOAD)
    mqtt_client = FakeMqttClient([KeyboardInterrupt()])
    http_client = FakeHttpClientWithClose([])
    cache_client = FakeCacheClient()

    # Should not raise - KeyboardInterrupt is handled gracefully.
    serve(mqtt_client, handler, http_client, cache_client)

    assert mqtt_client.closed is True
    assert http_client.closed is True
    assert cache_client.closed is True


def test_serve_closes_resources_and_reraises_on_unhandled_error():
    _discovery, handler = make_handler(HEADER_PAYLOAD)
    mqtt_client = FakeMqttClient(
        [], subscribe_error=RuntimeError("subscription rejected")
    )
    http_client = FakeHttpClientWithClose([])
    cache_client = FakeCacheClient()

    with pytest.raises(RuntimeError, match="rejected"):
        serve(mqtt_client, handler, http_client, cache_client)

    assert mqtt_client.closed is True
    assert http_client.closed is True
    assert cache_client.closed is True


def test_handler_with_notification_processor_wired_through_run():
    class FakeCacheClientRaises:
        def get(self, url: str) -> bytes:
            raise AssertionError("should not be reached for non-cache payload")

    discovery = PrematchFixtureDiscovery(
        http_client=FakeHttpClient([HEADER_PAYLOAD, HEADER_PAYLOAD])
    )
    processor = NotificationProcessor(cache_client=FakeCacheClientRaises())
    handler = PrematchHeaderRefreshHandler(
        discovery=discovery,
        notification_processor=processor,
    )

    mqtt_client = FakeMqttClient([make_message(b"not-a-cache-url")])

    with pytest.raises(StopTest):
        run(mqtt_client, handler)

    assert discovery.registry.get(1) is not None


# --- Graceful shutdown regression coverage (real MystakeMqttClient) ------
#
# These exercise the real receive-loop control flow (MystakeMqttClient's
# receive_publish / connect_with_retry / _reconnect_and_resubscribe),
# not just an outermost mocked KeyboardInterrupt, per the fix for
# real-network Ctrl+C shutdown not terminating promptly.


def test_run_stops_when_shutdown_requested_while_receive_is_blocked(monkeypatch):
    """Idle-blocked-receive scenario: request_shutdown() is called (as a
    signal handler would) while `receive_publish` is "blocked" (the fake
    receive_raw simulates the forced socket shutdown by raising
    ConnectionError, matching what a real closed socket produces)."""
    client = MystakeMqttClient()

    _discovery, handler = make_handler(HEADER_PAYLOAD)

    monkeypatch.setattr(client, "connect_with_retry", lambda: None)

    def fake_subscribe(topic: str, qos: int = 0) -> int:
        client._subscriptions[topic] = qos
        return 1

    monkeypatch.setattr(client, "subscribe", fake_subscribe)

    def fake_receive_raw() -> bytes:
        client.request_shutdown()
        raise ConnectionError("socket forcibly shut down by request_shutdown")

    monkeypatch.setattr(client, "receive_raw", fake_receive_raw)

    reconnect = Mock()
    monkeypatch.setattr(client, "_reconnect_and_resubscribe", reconnect)

    with pytest.raises(ListenerShutdown):
        run(client, handler)

    reconnect.assert_not_called()


def test_run_stops_between_messages_during_continuous_traffic(monkeypatch):
    """Continuous-traffic scenario: many PUBLISH packets keep arriving;
    shutdown is requested mid-burst and must stop dispatch at the next
    loop checkpoint rather than continuing to process traffic forever."""
    client = MystakeMqttClient()

    _discovery, handler = make_handler(HEADER_PAYLOAD, HEADER_PAYLOAD, HEADER_PAYLOAD)

    monkeypatch.setattr(client, "connect_with_retry", lambda: None)

    def fake_subscribe(topic: str, qos: int = 0) -> int:
        client._subscriptions[topic] = qos
        return 1

    monkeypatch.setattr(client, "subscribe", fake_subscribe)

    publish_packet = b"\x30\x18\x00\x0fprematch/headertrigger"

    calls = 0

    def fake_receive_raw() -> bytes:
        nonlocal calls
        calls += 1

        if calls == 3:
            client.request_shutdown()

        return publish_packet

    monkeypatch.setattr(client, "receive_raw", fake_receive_raw)

    with pytest.raises(ListenerShutdown):
        run(client, handler)

    # Messages 1 and 2 were dispatched normally; shutdown was requested
    # while handling message 3, and message 3 itself is still delivered
    # (deduplicated against message 2 here since the payload repeats),
    # but no further receive_raw call happens after that.
    assert calls == 3


def test_run_stops_after_active_refresh_completes_rather_than_swallowing_shutdown(
    monkeypatch,
):
    """Active-refresh scenario: shutdown is requested *during* a
    notification-triggered getheader/en refresh (simulating a signal
    landing mid-HTTP-call). The refresh must complete and apply normally
    (accuracy: never abandon an in-flight refresh), and the shutdown must
    still be observed at the next receive_publish checkpoint rather than
    being lost inside PrematchFixtureDiscovery.refresh()'s broad
    `except Exception`.
    """
    client = MystakeMqttClient()

    class ShutdownDuringRefreshHttpClient:
        def __init__(self, mqtt_client):
            self._mqtt_client = mqtt_client
            self._first_call = True

        def get_json(self, url: str):
            if self._first_call:
                self._first_call = False
                return HEADER_PAYLOAD
            self._mqtt_client.request_shutdown()
            return HEADER_PAYLOAD_WITH_ADDED_FIXTURE

    discovery = PrematchFixtureDiscovery(
        http_client=ShutdownDuringRefreshHttpClient(client)
    )
    handler = PrematchHeaderRefreshHandler(discovery=discovery)

    monkeypatch.setattr(client, "connect_with_retry", lambda: None)

    def fake_subscribe(topic: str, qos: int = 0) -> int:
        client._subscriptions[topic] = qos
        return 1

    monkeypatch.setattr(client, "subscribe", fake_subscribe)

    publish_packet = b"\x30\x18\x00\x0fprematch/headertrigger"

    monkeypatch.setattr(client, "receive_raw", Mock(return_value=publish_packet))

    with pytest.raises(ListenerShutdown):
        run(client, handler)

    # The in-flight refresh triggered by the notification completed and
    # was fully applied - the shutdown request did not corrupt or
    # truncate it.
    assert discovery.registry.get(2) is not None


def test_serve_closes_resources_on_listener_shutdown():
    """Cleanup executes correctly for a real ListenerShutdown, exactly
    like the existing KeyboardInterrupt case."""
    _discovery, handler = make_handler(HEADER_PAYLOAD)
    mqtt_client = FakeMqttClient([ListenerShutdown("shutdown requested")])
    http_client = FakeHttpClientWithClose([])
    cache_client = FakeCacheClient()

    serve(mqtt_client, handler, http_client, cache_client)

    assert mqtt_client.closed is True
    assert http_client.closed is True
    assert cache_client.closed is True


def test_install_shutdown_signal_handlers_calls_request_shutdown(monkeypatch):
    """SIGINT/SIGTERM both route to `request_shutdown()`, and neither
    signal handler itself raises (to avoid the active-refresh masking
    hazard covered above)."""
    client = MystakeMqttClient()

    registered = {}

    def fake_signal(signum, handler):
        registered[signum] = handler

    monkeypatch.setattr(signal, "signal", fake_signal)

    install_shutdown_signal_handlers(client)

    assert signal.SIGINT in registered
    assert signal.SIGTERM in registered
    assert client.shutdown_requested is False

    registered[signal.SIGINT](signal.SIGINT, None)

    assert client.shutdown_requested is True
