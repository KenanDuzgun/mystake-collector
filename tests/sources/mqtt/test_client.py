import socket
from unittest.mock import Mock

import pytest

from mystake.sources.mqtt.client import (
    ListenerShutdown,
    MystakeMqttClient,
)


def test_successful_subscribe_is_registered(
    monkeypatch,
) -> None:
    client = MystakeMqttClient()

    subscribe_once = Mock(return_value=7)

    monkeypatch.setattr(
        client,
        "_subscribe_once",
        subscribe_once,
    )

    packet_id = client.subscribe(
        "live/gamenew/123",
        qos=0,
    )

    assert packet_id == 7

    assert client._subscriptions == {
        "live/gamenew/123": 0,
    }


def test_failed_subscribe_is_not_registered(
    monkeypatch,
) -> None:
    client = MystakeMqttClient()

    def fail(
        topic: str,
        qos: int,
    ) -> int:
        raise RuntimeError("subscription failed")

    monkeypatch.setattr(
        client,
        "_subscribe_once",
        fail,
    )

    try:
        client.subscribe(
            "live/gamenew/123",
            qos=0,
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("Expected RuntimeError")

    assert client._subscriptions == {}


def test_successful_unsubscribe_removes_subscription(
    monkeypatch,
) -> None:
    client = MystakeMqttClient()

    client._subscriptions = {
        "live/gamenew/123": 0,
        "live/gamenew/456": 0,
    }

    unsubscribe_once = Mock(return_value=11)

    monkeypatch.setattr(
        client,
        "_unsubscribe_once",
        unsubscribe_once,
    )

    packet_id = client.unsubscribe("live/gamenew/123")

    assert packet_id == 11

    unsubscribe_once.assert_called_once_with(
        topic="live/gamenew/123",
    )

    assert client._subscriptions == {
        "live/gamenew/456": 0,
    }


def test_failed_unsubscribe_keeps_subscription(
    monkeypatch,
) -> None:
    client = MystakeMqttClient()

    client._subscriptions = {
        "live/gamenew/123": 0,
    }

    def fail(
        topic: str,
    ) -> int:
        raise RuntimeError("unsubscribe failed")

    monkeypatch.setattr(
        client,
        "_unsubscribe_once",
        fail,
    )

    try:
        client.unsubscribe("live/gamenew/123")
    except RuntimeError:
        pass
    else:
        raise AssertionError("Expected RuntimeError")

    assert client._subscriptions == {
        "live/gamenew/123": 0,
    }


def test_unsubscribe_unknown_topic_is_safe(
    monkeypatch,
) -> None:
    client = MystakeMqttClient()

    client._subscriptions = {
        "live/gamenew/456": 0,
    }

    unsubscribe_once = Mock(return_value=15)

    monkeypatch.setattr(
        client,
        "_unsubscribe_once",
        unsubscribe_once,
    )

    packet_id = client.unsubscribe("live/gamenew/123")

    assert packet_id == 15

    unsubscribe_once.assert_called_once_with(
        topic="live/gamenew/123",
    )

    assert client._subscriptions == {
        "live/gamenew/456": 0,
    }


def test_resubscribe_restores_active_topics(
    monkeypatch,
) -> None:
    client = MystakeMqttClient()

    client._subscriptions = {
        "live/gamenew/123": 0,
        "prematch/games": 0,
    }

    calls: list[tuple[str, int]] = []

    def fake_subscribe_once(
        topic: str,
        qos: int,
    ) -> int:
        calls.append((topic, qos))
        return len(calls)

    monkeypatch.setattr(
        client,
        "_subscribe_once",
        fake_subscribe_once,
    )

    client._resubscribe_active_topics()

    assert calls == [
        ("live/gamenew/123", 0),
        ("prematch/games", 0),
    ]


def test_unsubscribed_topic_is_not_resubscribed(
    monkeypatch,
) -> None:
    client = MystakeMqttClient()

    client._subscriptions = {
        "live/gamenew/123": 0,
        "live/gamenew/456": 0,
    }

    monkeypatch.setattr(
        client,
        "_unsubscribe_once",
        Mock(return_value=21),
    )

    client.unsubscribe("live/gamenew/123")

    calls: list[tuple[str, int]] = []

    def fake_subscribe_once(
        topic: str,
        qos: int,
    ) -> int:
        calls.append((topic, qos))
        return len(calls)

    monkeypatch.setattr(
        client,
        "_subscribe_once",
        fake_subscribe_once,
    )

    client._resubscribe_active_topics()

    assert calls == [
        ("live/gamenew/456", 0),
    ]


def test_receive_publish_recovers_connection(
    monkeypatch,
) -> None:
    client = MystakeMqttClient()

    publish_packet = b"\x30\x1a\x00\x0eprematch/gamescache:test"

    calls = 0

    def fake_receive_raw() -> bytes:
        nonlocal calls

        calls += 1

        if calls == 1:
            raise ConnectionError("test disconnect")

        return publish_packet

    reconnect = Mock()

    monkeypatch.setattr(
        client,
        "receive_raw",
        fake_receive_raw,
    )

    monkeypatch.setattr(
        client,
        "_reconnect_and_resubscribe",
        reconnect,
    )

    message = client.receive_publish()

    reconnect.assert_called_once_with()

    assert message.topic == "prematch/games"
    assert message.payload == b"cache:test"


def test_subscribe_preserves_publish_before_suback(
    monkeypatch,
) -> None:
    client = MystakeMqttClient()

    publish_packet = b"\x30\x1a\x00\x0eprematch/gamescache:test"

    suback_packet = b"\x90\x03\x00\x01\x00"

    ws = Mock()

    ws.recv.side_effect = [
        publish_packet,
        suback_packet,
    ]

    client.websocket = ws

    packet_id = client.subscribe(
        "live/gamenew/123",
        qos=0,
    )

    assert packet_id == 1
    assert client._subscriptions == {
        "live/gamenew/123": 0,
    }

    message = client.receive_publish()

    assert message.topic == "prematch/games"
    assert message.payload == b"cache:test"


def test_subscribe_ignores_pingresp_while_waiting_for_suback(
    monkeypatch,
) -> None:
    client = MystakeMqttClient()

    pingresp_packet = b"\xd0\x00"

    suback_packet = b"\x90\x03\x00\x01\x00"

    ws = Mock()

    ws.recv.side_effect = [
        pingresp_packet,
        suback_packet,
    ]

    client.websocket = ws

    packet_id = client.subscribe(
        "live/gamenew/123",
        qos=0,
    )

    assert packet_id == 1
    assert client._subscriptions == {
        "live/gamenew/123": 0,
    }


def test_unsubscribe_preserves_publish_before_unsuback(
    monkeypatch,
) -> None:
    client = MystakeMqttClient()

    client._subscriptions = {
        "live/gamenew/123": 0,
    }

    publish_packet = b"\x30\x1a\x00\x0eprematch/gamescache:test"

    unsuback_packet = b"\xb0\x02\x00\x01"

    ws = Mock()

    ws.recv.side_effect = [
        publish_packet,
        unsuback_packet,
    ]

    client.websocket = ws

    packet_id = client.unsubscribe("live/gamenew/123")

    assert packet_id == 1
    assert client._subscriptions == {}

    message = client.receive_publish()

    assert message.topic == "prematch/games"
    assert message.payload == b"cache:test"


# --- Graceful shutdown regression coverage -------------------------------


def test_request_shutdown_closes_underlying_socket() -> None:
    client = MystakeMqttClient()

    sock = Mock()
    client.websocket = Mock(sock=sock)

    client.request_shutdown()

    assert client.shutdown_requested is True
    sock.shutdown.assert_called_once_with(socket.SHUT_RDWR)


def test_request_shutdown_without_connection_is_safe() -> None:
    client = MystakeMqttClient()

    client.request_shutdown()

    assert client.shutdown_requested is True


def test_request_shutdown_is_idempotent() -> None:
    client = MystakeMqttClient()

    sock = Mock()
    client.websocket = Mock(sock=sock)

    client.request_shutdown()
    client.request_shutdown()

    assert client.shutdown_requested is True
    assert sock.shutdown.call_count == 2


def test_request_shutdown_swallows_socket_shutdown_errors() -> None:
    client = MystakeMqttClient()

    sock = Mock()
    sock.shutdown.side_effect = OSError("socket already closed")
    client.websocket = Mock(sock=sock)

    client.request_shutdown()

    assert client.shutdown_requested is True


def test_receive_publish_raises_listener_shutdown_when_already_requested(
    monkeypatch,
) -> None:
    """Shutdown requested before the loop even attempts a receive (covers
    the idle-blocked-receive case: the forced socket shutdown turns any
    blocked/retried `receive_raw()` into this state)."""
    client = MystakeMqttClient()
    client.request_shutdown()

    receive_raw = Mock()

    monkeypatch.setattr(client, "receive_raw", receive_raw)

    with pytest.raises(ListenerShutdown):
        client.receive_publish()

    receive_raw.assert_not_called()


def test_receive_publish_stops_between_continuous_messages_once_shutdown_requested(
    monkeypatch,
) -> None:
    """Shutdown requested mid-burst of continuous traffic: the in-flight
    message already read is still delivered, but the *next* call must
    stop instead of processing further traffic."""
    client = MystakeMqttClient()

    publish_packet = b"\x30\x1a\x00\x0eprematch/gamescache:test"

    calls = 0

    def fake_receive_raw() -> bytes:
        nonlocal calls
        calls += 1
        return publish_packet

    monkeypatch.setattr(client, "receive_raw", fake_receive_raw)

    message = client.receive_publish()
    assert message.topic == "prematch/games"

    client.request_shutdown()

    with pytest.raises(ListenerShutdown):
        client.receive_publish()

    assert calls == 1


def test_receive_publish_does_not_reconnect_after_shutdown_requested(
    monkeypatch,
) -> None:
    """No reconnection after shutdown begins: a connection loss observed
    once shutdown has been requested must raise instead of triggering
    `_reconnect_and_resubscribe`."""
    client = MystakeMqttClient()
    client.request_shutdown()

    monkeypatch.setattr(
        client,
        "receive_raw",
        Mock(side_effect=ConnectionError("closed by request_shutdown")),
    )

    reconnect = Mock()
    monkeypatch.setattr(client, "_reconnect_and_resubscribe", reconnect)

    with pytest.raises(ListenerShutdown):
        client.receive_publish()

    reconnect.assert_not_called()


def test_connection_loss_without_shutdown_still_reconnects(
    monkeypatch,
) -> None:
    """Existing dispatch/reconnect behavior is unchanged when shutdown was
    never requested."""
    client = MystakeMqttClient()

    publish_packet = b"\x30\x1a\x00\x0eprematch/gamescache:test"

    calls = 0

    def fake_receive_raw() -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("test disconnect")
        return publish_packet

    monkeypatch.setattr(client, "receive_raw", fake_receive_raw)
    reconnect = Mock()
    monkeypatch.setattr(client, "_reconnect_and_resubscribe", reconnect)

    message = client.receive_publish()

    reconnect.assert_called_once_with()
    assert message.topic == "prematch/games"


def test_connect_with_retry_stops_without_reconnecting_once_shutdown_requested(
    monkeypatch,
) -> None:
    client = MystakeMqttClient()

    def fake_connect_and_request_shutdown() -> None:
        client.request_shutdown()
        raise RuntimeError("connection refused")

    connect = Mock(side_effect=fake_connect_and_request_shutdown)
    monkeypatch.setattr(client, "connect", connect)

    with pytest.raises(ListenerShutdown):
        client.connect_with_retry()

    # Only the single attempt whose failure requested shutdown; no
    # further retry attempts and no real backoff sleep occurred.
    assert connect.call_count == 1


def test_reconnect_and_resubscribe_stops_without_retrying_once_shutdown_requested(
    monkeypatch,
) -> None:
    client = MystakeMqttClient()

    def fake_connect_and_request_shutdown() -> None:
        client.request_shutdown()
        raise RuntimeError("connection refused")

    connect = Mock(side_effect=fake_connect_and_request_shutdown)
    monkeypatch.setattr(client, "connect", connect)

    with pytest.raises(ListenerShutdown):
        client._reconnect_and_resubscribe()

    assert connect.call_count == 1


def test_repeated_shutdown_requests_during_retry_backoff_are_safe(
    monkeypatch,
) -> None:
    """Repeated shutdown requests (e.g. a user pressing Ctrl+C twice) must
    not raise or misbehave, and must not extend the retry backoff wait."""
    client = MystakeMqttClient()

    connect = Mock(side_effect=RuntimeError("connection refused"))
    monkeypatch.setattr(client, "connect", connect)

    client.request_shutdown()
    client.request_shutdown()

    with pytest.raises(ListenerShutdown):
        client.connect_with_retry()

    connect.assert_not_called()
