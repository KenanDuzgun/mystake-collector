import logging
import random
import socket
import ssl
import struct
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

import websocket

from mystake.config import (
    MQTT_ACK_TIMEOUT_SECONDS,
    MQTT_KEEP_ALIVE_SECONDS,
    MQTT_WEBSOCKET_SUBPROTOCOL,
    MQTT_WEBSOCKET_URL,
)
from mystake.sources.mqtt.message import (
    MqttPublishMessage,
)
from mystake.sources.mqtt.protocol import (
    build_connect_packet,
    build_pingreq_packet,
    build_subscribe_packet,
    build_unsubscribe_packet,
    create_client_id,
    is_pingresp,
    is_successful_connack,
    is_successful_suback,
    is_successful_unsuback,
    parse_publish_packet,
)

logger = logging.getLogger(__name__)

# MQTT control packet types (fixed header high nibble).
_PACKET_TYPE_PUBLISH = 3
_PACKET_TYPE_SUBACK = 9
_PACKET_TYPE_UNSUBACK = 11

# Phase 5C: hard bound on `_pending_packets` so a pathological run of
# many PUBLISH packets arriving while a SUBSCRIBE/UNSUBSCRIBE caller is
# self-pumping for its own ack cannot grow memory without bound. In
# real operation this queue holds at most a handful of packets - a
# SUBACK/UNSUBACK round trip is fast - so hitting this bound would
# itself be a symptom worth the loud error log.
_MAX_PENDING_PACKETS = 1000


class ListenerShutdown(Exception):
    """
    Raised out of `receive_publish`/`connect_with_retry`/reconnect loops
    once `request_shutdown()` has been called, so a caller (e.g. a
    signal handler-driven listener) unwinds instead of retrying or
    reconnecting.
    """


@dataclass
class _AckWaiter:
    """
    One in-flight SUBSCRIBE/UNSUBSCRIBE's wait for its SUBACK/UNSUBACK.

    Populated by whichever thread is currently reading the socket (see
    `MystakeMqttClient._read_dispatch_loop`/`_dispatch_ack`) - which may
    or may not be the same thread that is waiting on `event`.
    """

    event: threading.Event = field(default_factory=threading.Event)
    response: bytes | None = None


class MystakeMqttClient:
    def __init__(
        self,
        reconnect_initial_delay: float = 1.0,
        reconnect_max_delay: float = 30.0,
    ) -> None:
        self.client_id = create_client_id()
        self.websocket: websocket.WebSocket | None = None

        self.reconnect_initial_delay = reconnect_initial_delay
        self.reconnect_max_delay = reconnect_max_delay

        self._next_packet_id = 1

        self._awaiting_pingresp = False
        self._last_pingreq_at: float | None = None

        # Phase 6B diagnostics: connection-lifecycle timing, populated
        # in `connect()`/`_ws_recv_raw()`/`_send_packet()`, used only to
        # log lifetime/gap measurements - never read for control flow.
        self._connected_at: float | None = None
        self._last_recv_at: float | None = None
        self._last_send_at: float | None = None

        # Phase 6C diagnostics: `_last_publish_at` is updated only from
        # `_handle_frame()` when an MQTT PUBLISH is actually dispatched,
        # so "time since last application data" cannot be confused with
        # "time since last physical frame" (`_last_recv_at`, which also
        # advances on PINGRESP and the WebSocket CLOSE frame itself).
        # `_close_frame_observed`/`_close_code`/`_close_reason` record
        # what `_ws_recv_raw()` decoded from an actual WebSocket CLOSE
        # control frame, if one was observed, for the current
        # connection - reset on each `connect()`. All of these are
        # diagnostic only, never read for control flow.
        self._last_publish_at: float | None = None
        self._close_frame_observed = False
        self._close_code: int | None = None
        self._close_reason: str | None = None

        self._subscriptions: dict[str, int] = {}

        self._pending_packets: deque[bytes] = deque()

        self._shutdown_event = threading.Event()

        # Phase 5C: the physical WebSocket is single-reader.
        # `_reader_lock` is held for as long as a thread is actively
        # pumping `ws.recv()` - either the long-running
        # `receive_publish()`/`receive_raw()` loop, or a
        # SUBSCRIBE/UNSUBSCRIBE caller that found no one else already
        # reading and stepped in to self-pump until its own ack
        # arrives (see `_send_and_await_ack`). Whichever thread holds
        # it is the *only* thread ever calling `ws.recv()` - there is
        # never a concurrent socket read.
        #
        # Sending is independent of reading and guarded by its own
        # short-lived `_send_lock`: a SUBSCRIBE/UNSUBSCRIBE/PINGREQ
        # send only has to wait for another physical `send_binary()`
        # call to finish (microseconds), never for a blocking
        # `ws.recv()` (up to the read timeout) to return. That
        # decoupling is the actual Phase 5C fix: the previous shared
        # `_io_lock` serialized sends against the blocking recv, so a
        # SUBSCRIBE could not reach the wire until an in-flight,
        # up-to-read-timeout `recv()` returned and released it - the
        # root cause of the observed ~25s SUBSCRIBE->SUBACK delay.
        self._reader_lock = threading.Lock()
        self._send_lock = threading.Lock()

        # Packet-ID -> waiter for a caller currently blocked on that
        # SUBACK/UNSUBACK. Populated by `_send_and_await_ack`, consumed
        # by `_dispatch_ack` (called from whichever thread is currently
        # pumping the socket).
        self._ack_waiters: dict[int, _AckWaiter] = {}
        self._ack_lock = threading.Lock()

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown_event.is_set()

    def request_shutdown(self) -> None:
        """
        Idempotent. Signals every retry/receive loop to stop and forces
        any currently- or soon-to-be-blocked `recv()` to fail fast
        instead of blocking again or reconnecting.

        A plain flag is not enough on its own: a blocked read may not
        return until data arrives or a read-timeout elapses. Forcibly
        shutting down the raw socket (not the WebSocket-level graceful
        `close()`, which itself sends a CLOSE frame and blocks waiting
        for the peer's reply) makes any in-flight or retried `recv()`
        on that socket return immediately with an OS-level error,
        regardless of which layer (raw socket, TLS, the `selectors`
        retry loop in the `websocket` library) it is blocked in.

        Also wakes any SUBSCRIBE/UNSUBSCRIBE caller that is blocked on
        `_AckWaiter.event` without itself holding `_reader_lock` (i.e.
        it lost the race to become the active pumper): without this,
        that caller would otherwise sit out the full
        `MQTT_ACK_TIMEOUT_SECONDS` before noticing shutdown.
        """
        self._shutdown_event.set()

        with self._ack_lock:
            waiters = list(self._ack_waiters.values())

        for waiter in waiters:
            waiter.event.set()

        ws = self.websocket

        if ws is None:
            return

        sock = getattr(ws, "sock", None)

        if sock is None:
            return

        try:
            sock.shutdown(socket.SHUT_RDWR)

        except OSError:
            logger.debug(
                "MQTT socket shutdown() during request_shutdown() "
                "raised (socket likely already closed)",
                exc_info=True,
            )

    def _send_packet(self, ws: websocket.WebSocket, packet: bytes) -> None:
        with self._send_lock:
            ws.send_binary(packet)
            self._last_send_at = time.monotonic()

    def _ws_recv_raw(self, ws: websocket.WebSocket):
        """
        The only place that ever calls into the WebSocket read path.
        Callers must hold `_reader_lock` (or otherwise know they are
        the sole reader, e.g. during the CONNACK handshake in
        `connect()` before any other thread can reference the new
        socket).

        Phase 6C: uses `recv_data_frame(control_frame=True)` instead of
        `recv()` so an actual WebSocket CLOSE control frame's code/
        reason can be captured (see `_record_close_frame`) - `recv()`
        (installed `websocket-client` 1.9.2) already unconditionally
        consumed the CLOSE frame internally and collapsed it to `""`
        before returning, discarding the code/reason it carried.
        `control_frame=True` also surfaces PING/PONG control frames,
        which are not MQTT data and are just skipped here (the library
        has already auto-ponged a PING by the time it returns one) -
        this loop, not the caller, absorbs them, so every caller sees
        exactly the same contract as before: MQTT binary payload bytes,
        text (raising below, as before), or `""` for a closed
        connection.
        """
        while True:
            opcode, frame = ws.recv_data_frame(control_frame=True)

            self._last_recv_at = time.monotonic()

            if opcode == websocket.ABNF.OPCODE_CLOSE:
                self._record_close_frame(frame.data)
                return ""

            if opcode == websocket.ABNF.OPCODE_TEXT:
                data = frame.data
                return data.decode("utf-8") if isinstance(data, bytes) else data

            if opcode == websocket.ABNF.OPCODE_BINARY:
                return frame.data

            logger.debug(
                "Ignoring WebSocket control frame opcode=%s",
                opcode,
            )

    def _record_close_frame(self, data: bytes) -> None:
        """
        Decode an actual WebSocket CLOSE control frame's payload (2-byte
        big-endian status code + optional UTF-8 reason, per RFC 6455
        §5.5.1) - called only when `_ws_recv_raw` observed
        `ABNF.OPCODE_CLOSE` on the wire, never inferred from a plain
        transport EOF (see `WebSocketConnectionClosedException`
        handling in `_read_dispatch_loop`, which never calls this).
        """
        self._close_frame_observed = True

        if data and len(data) >= 2:
            (self._close_code,) = struct.unpack("!H", data[:2])
            self._close_reason = data[2:].decode("utf-8", errors="replace") or None
        else:
            self._close_code = None
            self._close_reason = None

        logger.info(
            "MQTT_WEBSOCKET_CLOSE_FRAME_OBSERVED code=%s reason=%r",
            self._close_code if self._close_code is not None else "unknown",
            self._close_reason,
        )

    def _raise_if_shutdown_requested(self) -> None:
        if self._shutdown_event.is_set():
            raise ListenerShutdown("Shutdown requested")

    def _wait_or_raise_if_shutdown(
        self,
        seconds: float,
    ) -> None:
        if self._shutdown_event.wait(seconds):
            raise ListenerShutdown("Shutdown requested during MQTT retry backoff")

    def receive_publish(
        self,
    ) -> MqttPublishMessage:
        while True:
            self._raise_if_shutdown_requested()

            try:
                packet = self.receive_raw()

            except ConnectionError as exc:
                if self._shutdown_event.is_set():
                    raise ListenerShutdown(
                        "Shutdown requested while MQTT connection was lost"
                    ) from exc

                logger.warning(
                    "MQTT connection lost: %s",
                    exc,
                )

                self._reconnect_and_resubscribe()
                continue

            if is_pingresp(packet):
                self._awaiting_pingresp = False
                self._last_pingreq_at = None

                logger.info("MQTT PINGRESP received")
                continue

            packet_type = packet[0] >> 4

            if packet_type == _PACKET_TYPE_PUBLISH:
                return parse_publish_packet(packet)

            logger.debug(
                "Ignoring non-PUBLISH MQTT packet type=%s raw=%s",
                packet_type,
                packet.hex(" "),
            )

    def connect(self) -> None:
        logger.info(
            "Connecting to MyStake MQTT WebSocket: %s",
            MQTT_WEBSOCKET_URL,
        )

        ws = websocket.create_connection(
            MQTT_WEBSOCKET_URL,
            subprotocols=[MQTT_WEBSOCKET_SUBPROTOCOL],
            timeout=10,
            sslopt={
                "cert_reqs": ssl.CERT_REQUIRED,
            },
        )

        self.websocket = ws

        logger.info(
            "WebSocket connected. subprotocol=%s client_id=%s",
            ws.getsubprotocol(),
            self.client_id,
        )

        connect_packet = build_connect_packet(self.client_id)

        self._send_packet(ws, connect_packet)

        response = self._ws_recv_raw(ws)

        if response == "":
            raise ConnectionError("MQTT WebSocket closed during CONNACK")

        if isinstance(response, str):
            raise RuntimeError(
                f"Expected binary MQTT CONNACK, received text: {response}"
            )

        if not is_successful_connack(response):
            raise RuntimeError(
                f"MQTT CONNECT rejected or unexpected response: {response.hex(' ')}"
            )

        logger.info("MQTT CONNACK accepted")

        read_timeout = MQTT_KEEP_ALIVE_SECONDS / 2

        ws.settimeout(read_timeout)

        self._awaiting_pingresp = False
        self._last_pingreq_at = None
        self._connected_at = time.monotonic()
        self._last_recv_at = self._connected_at
        self._last_send_at = self._connected_at
        self._last_publish_at = None
        self._close_frame_observed = False
        self._close_code = None
        self._close_reason = None

        logger.info(
            "MQTT keepalive enabled keep_alive=%ss read_timeout=%.1fs",
            MQTT_KEEP_ALIVE_SECONDS,
            read_timeout,
        )

    def connect_with_retry(self) -> None:
        delay = self.reconnect_initial_delay

        while True:
            self._raise_if_shutdown_requested()

            try:
                self.connect()
                return

            except Exception:
                logger.exception("MQTT connection failed")

                self.close()

                sleep_seconds = self._retry_sleep_seconds(delay)

                logger.warning(
                    "Retrying MQTT connection in %.2f seconds",
                    sleep_seconds,
                )

                self._wait_or_raise_if_shutdown(sleep_seconds)

                delay = min(
                    delay * 2,
                    self.reconnect_max_delay,
                )

    def subscribe(
        self,
        topic: str,
        qos: int = 0,
    ) -> int:
        packet_id = self._subscribe_once(
            topic=topic,
            qos=qos,
        )

        self._subscriptions[topic] = qos

        return packet_id

    def unsubscribe(
        self,
        topic: str,
    ) -> int:
        packet_id = self._unsubscribe_once(
            topic=topic,
        )

        self._subscriptions.pop(
            topic,
            None,
        )

        return packet_id

    def receive_raw(self) -> bytes:
        if self._pending_packets:
            return self._pending_packets.popleft()

        ws = self._require_connection()

        with self._reader_lock:
            # Another thread may have drained/refilled `_pending_packets`
            # (via a self-pumping SUBSCRIBE/UNSUBSCRIBE) between the
            # check above and acquiring the lock.
            if self._pending_packets:
                return self._pending_packets.popleft()

            packet = self._read_dispatch_loop(
                ws,
                stop_predicate=lambda: False,
                queue_publish=False,
            )

            assert packet is not None
            return packet

    def listen(
        self,
        on_message: Callable[[bytes], None],
    ) -> None:
        logger.info("MQTT listen loop started")

        while True:
            message = self.receive_raw()

            on_message(message)

    def close(self) -> None:
        if self.websocket is None:
            self._pending_packets.clear()
            return

        lifetime = (
            time.monotonic() - self._connected_at
            if self._connected_at is not None
            else None
        )
        close_frame_observed = self._close_frame_observed
        close_code = self._close_code
        close_reason = self._close_reason

        try:
            self.websocket.close()

        finally:
            self.websocket = None
            self._awaiting_pingresp = False
            self._last_pingreq_at = None
            self._connected_at = None
            self._pending_packets.clear()

        logger.info(
            "WebSocket closed connection_lifetime=%s close_frame_observed=%s "
            "close_code=%s close_reason=%r",
            f"{lifetime:.1f}s" if lifetime is not None else "unknown",
            close_frame_observed,
            close_code if close_code is not None else "unknown",
            close_reason,
        )

    def _subscribe_once(
        self,
        topic: str,
        qos: int,
    ) -> int:
        ws = self._require_connection()

        packet_id = self._allocate_packet_id()

        packet = build_subscribe_packet(
            topic=topic,
            packet_id=packet_id,
            qos=qos,
        )

        requested_at = time.monotonic()
        thread_name = threading.current_thread().name

        logger.info(
            "SUBSCRIBE_REQUESTED topic=%s qos=%s packet_id=%s thread=%s",
            topic,
            qos,
            packet_id,
            thread_name,
        )

        response = self._send_and_await_ack(
            ws,
            packet,
            packet_id,
            topic=topic,
            stage_prefix="SUBSCRIBE",
            ack_description="SUBACK",
            thread_name=thread_name,
        )

        if not is_successful_suback(
            response,
            expected_packet_id=packet_id,
        ):
            raise RuntimeError(
                f"MQTT subscription rejected or unexpected SUBACK: {response.hex(' ')}"
            )

        logger.info(
            "SUBSCRIBE_COMPLETED topic=%s packet_id=%s total_elapsed=%.3fs thread=%s",
            topic,
            packet_id,
            time.monotonic() - requested_at,
            thread_name,
        )

        return packet_id

    def _unsubscribe_once(
        self,
        topic: str,
    ) -> int:
        ws = self._require_connection()

        packet_id = self._allocate_packet_id()

        packet = build_unsubscribe_packet(
            topic=topic,
            packet_id=packet_id,
        )

        requested_at = time.monotonic()
        thread_name = threading.current_thread().name

        logger.info(
            "UNSUBSCRIBE_REQUESTED topic=%s packet_id=%s thread=%s",
            topic,
            packet_id,
            thread_name,
        )

        response = self._send_and_await_ack(
            ws,
            packet,
            packet_id,
            topic=topic,
            stage_prefix="UNSUBSCRIBE",
            ack_description="UNSUBACK",
            thread_name=thread_name,
        )

        if not is_successful_unsuback(
            response,
            expected_packet_id=packet_id,
        ):
            raise RuntimeError(
                "Unexpected MQTT packet "
                "while waiting for UNSUBACK: "
                f"{response.hex(' ')}"
            )

        logger.info(
            "UNSUBSCRIBE_COMPLETED topic=%s packet_id=%s total_elapsed=%.3fs thread=%s",
            topic,
            packet_id,
            time.monotonic() - requested_at,
            thread_name,
        )

        return packet_id

    def _send_and_await_ack(
        self,
        ws: websocket.WebSocket,
        packet: bytes,
        packet_id: int,
        *,
        topic: str,
        stage_prefix: str,
        ack_description: str,
        thread_name: str,
    ) -> bytes:
        """
        Send a SUBSCRIBE/UNSUBSCRIBE packet and block until its
        SUBACK/UNSUBACK has been dispatched to this call's waiter.

        This never calls `ws.recv()` itself unless it also becomes the
        active reader (see below) - it only ever blocks on an
        in-process `threading.Event`, so it can never hold up (or be
        held up by) the send of a physical packet, which is the actual
        Phase 5C fix: sending is decoupled from the (potentially long-
        blocking) socket read.

        Packet-ID ownership: a waiter is registered under `packet_id`
        for the duration of this call and removed in `finally`, so a
        SUBACK/UNSUBACK for *this* packet_id can only ever be
        delivered to *this* call, never cross-delivered to a
        differently-keyed concurrent SUBSCRIBE/UNSUBSCRIBE.

        Reader role: after sending, this call makes one non-blocking
        attempt to become the active socket reader (`_reader_lock`).
        - If it succeeds (no one else is currently reading, e.g. this
          is a synchronous/test call, or a reconnect resubscribe on a
          freshly reconnected socket with no other thread yet
          attached), it self-pumps `ws.recv()`, dispatching
          SUBACK/UNSUBACK to whichever waiter each belongs to (which
          may be a *different* concurrent SUBSCRIBE/UNSUBSCRIBE call)
          and queuing any PUBLISH it observes in `_pending_packets` for
          the next `receive_raw()` call - until its own ack arrives.
        - If it fails (some other thread - typically the main
          `receive_publish()` loop - is already the active reader),
          this call does not touch the socket at all; it just waits for
          that other thread to dispatch the ack to it.
        """
        self._raise_if_shutdown_requested()

        waiter = _AckWaiter()

        with self._ack_lock:
            self._ack_waiters[packet_id] = waiter

        try:
            lock_wait_start = time.monotonic()

            with self._send_lock:
                lock_acquired_at = time.monotonic()

                logger.info(
                    "%s_SEND_LOCK_ACQUIRED topic=%s packet_id=%s "
                    "lock_wait=%.3fs thread=%s",
                    stage_prefix,
                    topic,
                    packet_id,
                    lock_acquired_at - lock_wait_start,
                    thread_name,
                )

                try:
                    ws.send_binary(packet)

                except websocket.WebSocketConnectionClosedException as exc:
                    raise ConnectionError(
                        f"MQTT WebSocket closed while sending {stage_prefix} "
                        f"topic={topic} packet_id={packet_id}"
                    ) from exc

                except OSError as exc:
                    raise ConnectionError(
                        f"MQTT WebSocket network error while sending "
                        f"{stage_prefix} topic={topic} packet_id={packet_id}"
                    ) from exc

                sent_at = time.monotonic()

            logger.info(
                "%s_PACKET_SENT topic=%s packet_id=%s send_duration=%.3fs thread=%s",
                stage_prefix,
                topic,
                packet_id,
                sent_at - lock_acquired_at,
                thread_name,
            )

            became_reader = self._reader_lock.acquire(blocking=False)

            if became_reader:
                try:
                    self._read_dispatch_loop(
                        ws,
                        stop_predicate=waiter.event.is_set,
                        queue_publish=True,
                    )
                finally:
                    self._reader_lock.release()

            acked = waiter.event.wait(timeout=MQTT_ACK_TIMEOUT_SECONDS)

            ack_received_at = time.monotonic()

            if self._shutdown_event.is_set() and waiter.response is None:
                raise ListenerShutdown(
                    "Shutdown requested while waiting for MQTT "
                    f"{ack_description} packet_id={packet_id}"
                )

            if not acked:
                raise ConnectionError(
                    f"Timed out waiting for MQTT {ack_description} "
                    f"packet_id={packet_id} topic={topic} "
                    f"after {MQTT_ACK_TIMEOUT_SECONDS}s"
                )

            logger.info(
                "%s_RECEIVED topic=%s packet_id=%s ack_wait=%.3fs "
                "became_reader=%s thread=%s",
                ack_description,
                topic,
                packet_id,
                ack_received_at - sent_at,
                became_reader,
                thread_name,
            )

            assert waiter.response is not None
            return waiter.response

        finally:
            with self._ack_lock:
                self._ack_waiters.pop(packet_id, None)

    def _read_dispatch_loop(
        self,
        ws: websocket.WebSocket,
        *,
        stop_predicate: Callable[[], bool],
        queue_publish: bool,
    ) -> bytes | None:
        """
        Read physical frames off `ws` (caller must hold `_reader_lock`)
        until either a PUBLISH is found (returned immediately when
        `queue_publish` is False), or `stop_predicate()` becomes true
        (used by a self-pumping SUBSCRIBE/UNSUBSCRIBE to stop once its
        own ack has arrived).

        Any PUBLISH observed is either returned directly (the main
        `receive_raw()` reader) or queued to `_pending_packets` (a
        self-pumping ack-waiter, which cannot return a PUBLISH from
        deep inside its own call stack) - it is never dropped.
        SUBACK/UNSUBACK packets are always dispatched via
        `_dispatch_ack`, regardless of which of the two roles above is
        calling.
        """
        while True:
            try:
                message = self._ws_recv_raw(ws)

            except websocket.WebSocketTimeoutException:
                self._handle_read_timeout()

                if stop_predicate():
                    return None

                continue

            except websocket.WebSocketConnectionClosedException as exc:
                raise ConnectionError(
                    f"MQTT WebSocket connection closed {self._lifecycle_context()}"
                ) from exc

            except OSError as exc:
                raise ConnectionError(
                    f"MQTT WebSocket network error {self._lifecycle_context()}"
                ) from exc

            if message == "":
                raise ConnectionError(
                    "MQTT WebSocket connection closed by remote peer "
                    f"{self._lifecycle_context()}"
                )

            if isinstance(message, str):
                raise RuntimeError(
                    f"Expected binary MQTT message, received text: {message}"
                )

            publish = self._handle_frame(message)

            if publish is not None:
                if queue_publish:
                    self._enqueue_pending_packet(publish)
                else:
                    return publish

            if stop_predicate():
                return None

    def _handle_frame(self, message: bytes) -> bytes | None:
        """
        Handle one physical frame already known to be a non-empty
        binary MQTT packet. Returns the raw PUBLISH bytes if `message`
        is a PUBLISH, otherwise dispatches it internally (PINGRESP
        keepalive state, SUBACK/UNSUBACK ack delivery) and returns
        None.
        """
        if is_pingresp(message):
            self._awaiting_pingresp = False
            self._last_pingreq_at = None

            logger.info("MQTT PINGRESP received")
            return None

        packet_type = message[0] >> 4

        if packet_type in (_PACKET_TYPE_SUBACK, _PACKET_TYPE_UNSUBACK):
            self._dispatch_ack(message)
            return None

        if packet_type == _PACKET_TYPE_PUBLISH:
            self._last_publish_at = time.monotonic()
            return message

        logger.debug(
            "Ignoring non-PUBLISH/ack MQTT packet type=%s raw=%s",
            packet_type,
            message.hex(" "),
        )
        return None

    def _dispatch_ack(self, message: bytes) -> None:
        """
        Route a SUBACK/UNSUBACK to the waiter registered for its
        packet_id (both packet types carry the packet_id at the same
        offset - see `protocol.parse_suback`/`is_successful_unsuback`).
        An ack with no matching waiter (already timed out, or a stray
        broker retransmit) is logged and dropped - there is nothing
        else safe to do with it.
        """
        if len(message) < 4:
            logger.warning(
                "Received malformed MQTT ack (too short): %s",
                message.hex(" "),
            )
            return

        packet_id = int.from_bytes(message[2:4], byteorder="big")

        with self._ack_lock:
            waiter = self._ack_waiters.get(packet_id)

        if waiter is None:
            logger.warning(
                "Received MQTT ack for unknown/expired packet_id=%s raw=%s",
                packet_id,
                message.hex(" "),
            )
            return

        waiter.response = message
        waiter.event.set()

    def _enqueue_pending_packet(self, packet: bytes) -> None:
        if len(self._pending_packets) >= _MAX_PENDING_PACKETS:
            self._pending_packets.popleft()

            logger.error(
                "MQTT pending-packet queue exceeded bound=%s; dropped oldest "
                "queued PUBLISH to bound memory use",
                _MAX_PENDING_PACKETS,
            )

        self._pending_packets.append(packet)

    def _reconnect_and_resubscribe(
        self,
    ) -> None:
        delay = self.reconnect_initial_delay

        while True:
            self._raise_if_shutdown_requested()

            self.close()

            reconnect_started_at = time.monotonic()

            try:
                logger.info("MQTT_RECONNECT_INITIATED")

                self.connect()

                self._resubscribe_active_topics()

                logger.info(
                    "MQTT_RECONNECT_COMPLETED duration=%.3fs",
                    time.monotonic() - reconnect_started_at,
                )

                return

            except Exception:
                logger.exception("MQTT reconnect/resubscribe failed")

                self.close()

                sleep_seconds = self._retry_sleep_seconds(delay)

                logger.warning(
                    "Retrying MQTT reconnect in %.2f seconds",
                    sleep_seconds,
                )

                self._wait_or_raise_if_shutdown(sleep_seconds)

                delay = min(
                    delay * 2,
                    self.reconnect_max_delay,
                )

    def _resubscribe_active_topics(
        self,
    ) -> None:
        if not self._subscriptions:
            logger.info("No active MQTT subscriptions to restore")
            return

        subscriptions = tuple(self._subscriptions.items())

        logger.info(
            "Restoring %s MQTT subscription(s)",
            len(subscriptions),
        )

        for topic, qos in subscriptions:
            self._subscribe_once(
                topic=topic,
                qos=qos,
            )

        logger.info("MQTT subscriptions restored")

    def _handle_read_timeout(
        self,
    ) -> None:
        now = time.monotonic()

        if self._awaiting_pingresp:
            if self._last_pingreq_at is None:
                raise ConnectionError("MQTT keepalive state is invalid")

            elapsed = now - self._last_pingreq_at

            if elapsed >= MQTT_KEEP_ALIVE_SECONDS:
                raise ConnectionError("MQTT PINGRESP timeout")

            logger.debug(
                "Still waiting for MQTT PINGRESP elapsed=%.1fs",
                elapsed,
            )
            return

        ws = self._require_connection()

        packet = build_pingreq_packet()

        try:
            self._send_packet(ws, packet)

        except websocket.WebSocketConnectionClosedException as exc:
            raise ConnectionError(
                "MQTT WebSocket closed while sending PINGREQ"
            ) from exc

        except OSError as exc:
            raise ConnectionError("MQTT network error while sending PINGREQ") from exc

        self._awaiting_pingresp = True
        self._last_pingreq_at = now

        logger.info("MQTT PINGREQ sent")

    def _retry_sleep_seconds(
        self,
        delay: float,
    ) -> float:
        jitter = random.uniform(
            0,
            delay * 0.2,
        )

        return min(
            delay + jitter,
            self.reconnect_max_delay,
        )

    def _lifecycle_context(self) -> str:
        """
        Phase 6B/6C diagnostics: a short `key=value` fragment appended
        to connection-loss error messages so log lines self-report the
        connection's age, PINGREQ/PINGRESP state, and WebSocket CLOSE
        frame evidence (if any) without needing to cross-reference
        other log lines.

        `since_last_publish` is deliberately derived from
        `_last_publish_at` (set only in `_handle_frame` on an actual
        MQTT PUBLISH), not from `_last_recv_at` (advanced on every
        physical frame, including the CLOSE frame that would otherwise
        make application-data freshness look artificially current at
        the exact moment the connection dies).
        """
        now = time.monotonic()

        connection_lifetime = (
            f"{now - self._connected_at:.1f}s"
            if self._connected_at is not None
            else "unknown"
        )
        since_last_recv = (
            f"{now - self._last_recv_at:.1f}s"
            if self._last_recv_at is not None
            else "unknown"
        )
        since_last_publish = (
            f"{now - self._last_publish_at:.1f}s"
            if self._last_publish_at is not None
            else "none_yet"
        )
        close_frame = (
            f"code={self._close_code} reason={self._close_reason!r}"
            if self._close_frame_observed
            else "not_observed"
        )

        return (
            f"(connection_lifetime={connection_lifetime} "
            f"since_last_recv={since_last_recv} "
            f"since_last_publish={since_last_publish} "
            f"awaiting_pingresp={self._awaiting_pingresp} "
            f"close_frame={close_frame})"
        )

    def _require_connection(
        self,
    ) -> websocket.WebSocket:
        if self.websocket is None:
            raise ConnectionError("MQTT WebSocket is not connected")

        return self.websocket

    def _allocate_packet_id(self) -> int:
        packet_id = self._next_packet_id

        self._next_packet_id += 1

        if self._next_packet_id > 65535:
            self._next_packet_id = 1

        return packet_id
