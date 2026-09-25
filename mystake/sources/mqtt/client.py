import logging
import random
import socket
import ssl
import threading
import time
from collections import deque
from collections.abc import Callable

import websocket

from mystake.config import (
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


class ListenerShutdown(Exception):
    """
    Raised out of `receive_publish`/`connect_with_retry`/reconnect loops
    once `request_shutdown()` has been called, so a caller (e.g. a
    signal handler-driven listener) unwinds instead of retrying or
    reconnecting.
    """


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

        self._subscriptions: dict[str, int] = {}

        self._pending_packets: deque[bytes] = deque()

        self._shutdown_event = threading.Event()

        # Phase 5B: guards every physical socket read/write. The main
        # thread's `receive_publish()` loop and a background discovery/
        # handoff coordination thread (see
        # `mystake.pipeline.prematch_to_live_handoff`) may both call into
        # this client concurrently (the coordinator calls `subscribe()`
        # the moment it detects a live transition, without waiting for
        # `receive_publish()` to return). The lock is only held for the
        # duration of one `ws.recv()`/`ws.send_binary()` call - never
        # across a whole `receive_publish()`/`subscribe()` invocation -
        # so neither caller can starve the other indefinitely; a PUBLISH
        # observed by one thread while the other is mid SUBSCRIBE/
        # UNSUBSCRIBE still lands safely in `_pending_packets` via the
        # existing `_await_control_packet` handling.
        self._io_lock = threading.Lock()

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
        """
        self._shutdown_event.set()

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

    def _ws_send(self, ws: websocket.WebSocket, packet: bytes) -> None:
        with self._io_lock:
            ws.send_binary(packet)

    def _ws_recv(self, ws: websocket.WebSocket):
        with self._io_lock:
            return ws.recv()

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

            if packet_type == 3:
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

        self._ws_send(ws, connect_packet)

        response = self._ws_recv(ws)

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

        while True:
            try:
                message = self._ws_recv(ws)

            except websocket.WebSocketTimeoutException:
                self._handle_read_timeout()
                continue

            except websocket.WebSocketConnectionClosedException as exc:
                raise ConnectionError("MQTT WebSocket connection closed") from exc

            except OSError as exc:
                raise ConnectionError("MQTT WebSocket network error") from exc

            if message == "":
                raise ConnectionError("MQTT WebSocket connection closed by remote peer")

            if isinstance(message, str):
                raise RuntimeError(
                    f"Expected binary MQTT message, received text: {message}"
                )

            return message

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

        try:
            self.websocket.close()

        finally:
            self.websocket = None
            self._awaiting_pingresp = False
            self._last_pingreq_at = None
            self._pending_packets.clear()

        logger.info("WebSocket closed")

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

        logger.info(
            "Subscribing topic=%s qos=%s packet_id=%s",
            topic,
            qos,
            packet_id,
        )

        try:
            self._ws_send(ws, packet)

            response = self._await_control_packet(
                ws,
                description="SUBACK",
            )

        except websocket.WebSocketConnectionClosedException as exc:
            raise ConnectionError("MQTT WebSocket closed during SUBSCRIBE") from exc

        except OSError as exc:
            raise ConnectionError(
                "MQTT WebSocket network error during SUBSCRIBE"
            ) from exc

        if not is_successful_suback(
            response,
            expected_packet_id=packet_id,
        ):
            raise RuntimeError(
                f"MQTT subscription rejected or unexpected SUBACK: {response.hex(' ')}"
            )

        logger.info(
            "MQTT subscription accepted topic=%s packet_id=%s",
            topic,
            packet_id,
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

        logger.info(
            "Unsubscribing topic=%s packet_id=%s",
            topic,
            packet_id,
        )

        try:
            self._ws_send(ws, packet)

            response = self._await_control_packet(
                ws,
                description="UNSUBACK",
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

        except websocket.WebSocketConnectionClosedException as exc:
            raise ConnectionError("MQTT WebSocket closed during UNSUBSCRIBE") from exc

        except OSError as exc:
            raise ConnectionError(
                "MQTT WebSocket network error during UNSUBSCRIBE"
            ) from exc

        logger.info(
            "MQTT unsubscribe accepted topic=%s packet_id=%s",
            topic,
            packet_id,
        )

        return packet_id

    def _await_control_packet(
        self,
        ws: websocket.WebSocket,
        *,
        description: str,
    ) -> bytes:
        """
        Read packets from the WebSocket until a non-PUBLISH,
        non-PINGRESP control packet arrives (e.g. SUBACK/UNSUBACK).

        Any PUBLISH packet observed while waiting is queued in
        `_pending_packets` rather than dropped, since the broker may
        deliver PUBLISH packets for other active subscriptions before
        acknowledging the in-flight SUBSCRIBE/UNSUBSCRIBE.
        """
        while True:
            response = self._ws_recv(ws)

            if response == "":
                raise ConnectionError(
                    f"MQTT WebSocket closed while waiting for {description}"
                )

            if isinstance(response, str):
                raise RuntimeError(
                    "Expected binary MQTT message "
                    f"while waiting for {description}, "
                    f"received text: {response}"
                )

            if is_pingresp(response):
                self._awaiting_pingresp = False
                self._last_pingreq_at = None

                logger.info(
                    "MQTT PINGRESP received while waiting for %s",
                    description,
                )
                continue

            packet_type = response[0] >> 4

            if packet_type == 3:
                self._pending_packets.append(response)

                logger.debug(
                    "Queued MQTT PUBLISH while waiting for %s",
                    description,
                )
                continue

            return response

    def _reconnect_and_resubscribe(
        self,
    ) -> None:
        delay = self.reconnect_initial_delay

        while True:
            self._raise_if_shutdown_requested()

            self.close()

            try:
                logger.info("Attempting MQTT reconnect")

                self.connect()

                self._resubscribe_active_topics()

                logger.info("MQTT reconnect and resubscribe completed")

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
            self._ws_send(ws, packet)

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
