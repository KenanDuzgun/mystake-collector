import socket
import time
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


def test_send_lock_serializes_concurrent_sends() -> None:
    """
    Phase 5C: `_send_lock` still serializes concurrent physical
    `send_binary()` calls against each other (e.g. a PINGREQ from the
    reader loop racing a SUBSCRIBE send from a background thread), so
    two sends can never interleave mid-frame.
    """
    import threading
    import time as time_module

    client = MystakeMqttClient()

    events: list[str] = []
    events_lock = threading.Lock()

    class SlowSendWebSocket:
        def send_binary(self, packet):
            with events_lock:
                events.append("send-start")
            time_module.sleep(0.02)
            with events_lock:
                events.append("send-end")

    ws = SlowSendWebSocket()

    threads = [
        threading.Thread(target=client._send_packet, args=(ws, b"\x00")),
        threading.Thread(target=client._send_packet, args=(ws, b"\x01")),
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert events == ["send-start", "send-end", "send-start", "send-end"]


def test_send_is_not_blocked_by_concurrent_blocking_recv() -> None:
    """
    Phase 5C root-cause regression: sending must never wait on a
    concurrent, long-blocking `ws.recv()`. This is the actual defect
    behind the ~25s observed SUBSCRIBE->SUBACK delay - the old shared
    `_io_lock` serialized `_ws_send`/`_ws_recv`, so a SUBSCRIBE send
    could not reach the wire until an in-flight blocking `recv()`
    (held for up to the full socket read timeout) returned.
    """
    import threading

    client = MystakeMqttClient()

    recv_started = threading.Event()
    release_recv = threading.Event()
    send_completed = threading.Event()

    class BlockingRecvWebSocket:
        def recv(self):
            recv_started.set()
            release_recv.wait(timeout=5)
            return b"\x00"

        def send_binary(self, packet):
            send_completed.set()

    ws = BlockingRecvWebSocket()

    recv_thread = threading.Thread(target=client._ws_recv_raw, args=(ws,))
    recv_thread.start()

    assert recv_started.wait(timeout=2), "recv() never started"

    client._send_packet(ws, b"\x00")

    assert send_completed.is_set(), (
        "send blocked behind a concurrent blocking recv() - this is the "
        "Phase 5C regression"
    )

    release_recv.set()
    recv_thread.join(timeout=5)


# --- Phase 5C: single-reader / packet-dispatch regression coverage -------


class _QueueWebSocket:
    """
    Deterministic fake WebSocket for concurrency tests: `recv()` blocks
    on an internal queue fed by the test (no arbitrary sleeps needed
    for synchronization) and records whether two `recv()` calls were
    ever active at the same instant.
    """

    def __init__(self) -> None:
        import queue as queue_module
        import threading as threading_module

        self._queue = queue_module.Queue()
        self._lock = threading_module.Lock()
        self._recv_active = False
        self.concurrent_recv_detected = False
        self.sent_packets: list[bytes] = []

    def feed(self, message) -> None:
        self._queue.put(message)

    def recv(self):
        with self._lock:
            if self._recv_active:
                self.concurrent_recv_detected = True
            self._recv_active = True

        try:
            return self._queue.get(timeout=5)
        finally:
            with self._lock:
                self._recv_active = False

    def send_binary(self, packet) -> None:
        self.sent_packets.append(packet)


def test_no_concurrent_reads_suback_dispatched_while_publish_pending(
    monkeypatch,
) -> None:
    """
    Reproduces the real handoff scenario end to end: a "main loop"
    thread continuously calling `receive_raw()` (standing in for
    `receive_publish()`) while a background thread calls `subscribe()`
    concurrently. Covers, in one deterministic run: concurrent receive
    + subscribe, a PUBLISH observed by the reader before the SUBACK it
    is dispatching for another thread, and no concurrent socket reads.
    """
    import threading

    client = MystakeMqttClient()

    publish_packet = b"\x30\x1a\x00\x0eprematch/gamescache:test"
    suback_packet = b"\x90\x03\x00\x01\x00"

    ws = _QueueWebSocket()
    client.websocket = ws

    reader_results: list[bytes] = []

    def reader_loop() -> None:
        reader_results.append(client.receive_raw())
        reader_results.append(client.receive_raw())

    reader_thread = threading.Thread(target=reader_loop)
    reader_thread.start()

    # Let the reader become the active pumper and block inside recv().
    deadline = time.monotonic() + 2
    while not ws._recv_active and time.monotonic() < deadline:
        time.sleep(0.001)
    assert ws._recv_active, "reader thread never started its blocking recv()"

    subscribe_result: dict[str, int] = {}

    def do_subscribe() -> None:
        subscribe_result["packet_id"] = client.subscribe(
            "live/gamenew/123",
            qos=0,
        )

    subscribe_thread = threading.Thread(target=do_subscribe)
    subscribe_thread.start()

    # The SUBSCRIBE send must not need the reader thread's cooperation.
    deadline = time.monotonic() + 2
    while not ws.sent_packets and time.monotonic() < deadline:
        time.sleep(0.001)
    assert ws.sent_packets, "SUBSCRIBE packet was never sent"

    ws.feed(publish_packet)
    ws.feed(suback_packet)
    ws.feed(publish_packet)

    subscribe_thread.join(timeout=5)
    reader_thread.join(timeout=5)

    assert not subscribe_thread.is_alive()
    assert not reader_thread.is_alive()

    assert subscribe_result["packet_id"] == 1
    assert client._subscriptions == {"live/gamenew/123": 0}

    assert len(reader_results) == 2
    for message in reader_results:
        assert message == publish_packet

    assert ws.concurrent_recv_detected is False
    assert client._ack_waiters == {}


def test_packet_id_correlation_for_concurrent_subscribes(
    monkeypatch,
) -> None:
    """
    Two concurrent SUBSCRIBE calls must each receive only the SUBACK
    matching their own packet_id, even when the broker's responses
    arrive out of order.
    """
    import threading

    client = MystakeMqttClient()

    ws = _QueueWebSocket()
    client.websocket = ws

    results: dict[str, int] = {}
    errors: list[Exception] = []

    def subscribe(topic: str, key: str) -> None:
        try:
            results[key] = client.subscribe(topic, qos=0)
        except (RuntimeError, ConnectionError) as exc:  # pragma: no cover
            errors.append(exc)

    thread_a = threading.Thread(target=subscribe, args=("live/gamenew/1", "a"))
    thread_a.start()

    deadline = time.monotonic() + 2
    while len(ws.sent_packets) < 1 and time.monotonic() < deadline:
        time.sleep(0.001)

    thread_b = threading.Thread(target=subscribe, args=("live/gamenew/2", "b"))
    thread_b.start()

    deadline = time.monotonic() + 2
    while len(ws.sent_packets) < 2 and time.monotonic() < deadline:
        time.sleep(0.001)

    # Respond out of order: packet_id=2 (thread_b) acked before packet_id=1.
    suback_packet_id_2 = b"\x90\x03\x00\x02\x00"
    suback_packet_id_1 = b"\x90\x03\x00\x01\x00"

    ws.feed(suback_packet_id_2)
    ws.feed(suback_packet_id_1)

    thread_a.join(timeout=5)
    thread_b.join(timeout=5)

    assert errors == []
    assert results == {"a": 1, "b": 2}
    assert client._subscriptions == {
        "live/gamenew/1": 0,
        "live/gamenew/2": 0,
    }
    assert ws.concurrent_recv_detected is False
    assert client._ack_waiters == {}


def test_multiple_sequential_subscriptions_increment_packet_id() -> None:
    client = MystakeMqttClient()

    ws = Mock()
    ws.recv.side_effect = [
        b"\x90\x03\x00\x01\x00",
        b"\x90\x03\x00\x02\x00",
        b"\x90\x03\x00\x03\x00",
    ]
    client.websocket = ws

    packet_ids = [client.subscribe(f"live/gamenew/{n}", qos=0) for n in (1, 2, 3)]

    assert packet_ids == [1, 2, 3]
    assert client._subscriptions == {
        "live/gamenew/1": 0,
        "live/gamenew/2": 0,
        "live/gamenew/3": 0,
    }


def test_unsubscribe_ack_dispatched_by_concurrent_reader() -> None:
    import threading

    client = MystakeMqttClient()

    client._subscriptions = {"live/gamenew/123": 0}

    ws = _QueueWebSocket()
    client.websocket = ws

    reader_result: dict[str, bytes] = {}

    def reader() -> None:
        reader_result["packet"] = client.receive_raw()

    publish_packet = b"\x30\x1a\x00\x0eprematch/gamescache:test"

    reader_thread = threading.Thread(target=reader)
    reader_thread.start()

    deadline = time.monotonic() + 2
    while not ws._recv_active and time.monotonic() < deadline:
        time.sleep(0.001)

    unsubscribe_result: dict[str, int] = {}

    def do_unsubscribe() -> None:
        unsubscribe_result["packet_id"] = client.unsubscribe("live/gamenew/123")

    unsubscribe_thread = threading.Thread(target=do_unsubscribe)
    unsubscribe_thread.start()

    deadline = time.monotonic() + 2
    while not ws.sent_packets and time.monotonic() < deadline:
        time.sleep(0.001)

    unsuback_packet = b"\xb0\x02\x00\x01"

    ws.feed(unsuback_packet)
    ws.feed(publish_packet)

    unsubscribe_thread.join(timeout=5)
    reader_thread.join(timeout=5)

    assert unsubscribe_result["packet_id"] == 1
    assert client._subscriptions == {}
    assert reader_result["packet"] == publish_packet
    assert ws.concurrent_recv_detected is False


def test_ack_timeout_raises_connection_error(monkeypatch) -> None:
    import mystake.sources.mqtt.client as client_module

    monkeypatch.setattr(client_module, "MQTT_ACK_TIMEOUT_SECONDS", 0.05)

    client = MystakeMqttClient()

    ws = _QueueWebSocket()
    client.websocket = ws
    # Simulate another thread already owning the reader role, so this
    # call cannot self-pump and must rely purely on the ack wait.
    client._reader_lock.acquire()

    try:
        with pytest.raises(ConnectionError, match="Timed out waiting"):
            client.subscribe("live/gamenew/123", qos=0)
    finally:
        client._reader_lock.release()

    assert client._ack_waiters == {}
    assert client._subscriptions == {}


def test_shutdown_wakes_blocked_ack_waiter_promptly(monkeypatch) -> None:
    """
    A SUBSCRIBE/UNSUBSCRIBE caller that lost the race to become the
    active reader only waits on its ack `Event` - it must be woken by
    `request_shutdown()` promptly, not sit out the full ack timeout.
    """
    import threading

    import mystake.sources.mqtt.client as client_module

    monkeypatch.setattr(client_module, "MQTT_ACK_TIMEOUT_SECONDS", 5.0)

    client = MystakeMqttClient()

    ws = _QueueWebSocket()
    client.websocket = ws
    # Another thread owns the reader role, so subscribe() cannot
    # self-pump and must rely purely on the ack wait.
    client._reader_lock.acquire()

    result: dict[str, object] = {}

    def do_subscribe() -> None:
        try:
            client.subscribe("live/gamenew/123", qos=0)
        except Exception as exc:  # noqa: BLE001 - captured for assertion below
            result["error"] = exc

    subscribe_thread = threading.Thread(target=do_subscribe)
    subscribe_thread.start()

    deadline = time.monotonic() + 2
    while not client._ack_waiters and time.monotonic() < deadline:
        time.sleep(0.001)

    assert client._ack_waiters, "waiter was never registered"

    started = time.monotonic()
    client.request_shutdown()
    subscribe_thread.join(timeout=5)
    elapsed = time.monotonic() - started

    client._reader_lock.release()

    assert not subscribe_thread.is_alive()
    assert isinstance(result.get("error"), ListenerShutdown)
    assert elapsed < 2, "shutdown did not wake the blocked ack waiter promptly"


def test_pending_packet_queue_is_bounded() -> None:
    client = MystakeMqttClient()

    over_limit = 5

    from mystake.sources.mqtt.client import _MAX_PENDING_PACKETS

    for i in range(_MAX_PENDING_PACKETS + over_limit):
        client._enqueue_pending_packet(f"packet-{i}".encode())

    assert len(client._pending_packets) == _MAX_PENDING_PACKETS
    # oldest entries were dropped; the newest ones survive
    assert (
        client._pending_packets[-1]
        == f"packet-{_MAX_PENDING_PACKETS + over_limit - 1}".encode()
    )
