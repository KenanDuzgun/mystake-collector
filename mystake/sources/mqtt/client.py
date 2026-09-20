import logging
import random
import ssl
import time
from typing import Callable

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
    create_client_id,
    is_pingresp,
    is_successful_connack,
    is_successful_suback,
    parse_publish_packet,
)


logger = logging.getLogger(__name__)


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

    def receive_publish(
        self,
    ) -> MqttPublishMessage:
        while True:
            try:
                packet = self.receive_raw()

            except ConnectionError as exc:
                logger.warning(
                    "MQTT connection lost: %s",
                    exc,
                )

                self._reconnect_and_resubscribe()
                continue

            if is_pingresp(packet):
                self._awaiting_pingresp = False
                self._last_pingreq_at = None

                logger.info(
                    "MQTT PINGRESP received"
                )
                continue

            packet_type = packet[0] >> 4

            if packet_type == 3:
                return parse_publish_packet(
                    packet
                )

            logger.debug(
                "Ignoring non-PUBLISH MQTT packet "
                "type=%s raw=%s",
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
            subprotocols=[
                MQTT_WEBSOCKET_SUBPROTOCOL
            ],
            timeout=10,
            sslopt={
                "cert_reqs": ssl.CERT_REQUIRED,
            },
        )

        self.websocket = ws

        logger.info(
            "WebSocket connected. "
            "subprotocol=%s client_id=%s",
            ws.getsubprotocol(),
            self.client_id,
        )

        connect_packet = build_connect_packet(
            self.client_id
        )

        ws.send_binary(
            connect_packet
        )

        response = ws.recv()

        if response == "":
            raise ConnectionError(
                "MQTT WebSocket closed during CONNACK"
            )

        if isinstance(response, str):
            raise RuntimeError(
                "Expected binary MQTT CONNACK, "
                f"received text: {response}"
            )

        if not is_successful_connack(
            response
        ):
            raise RuntimeError(
                "MQTT CONNECT rejected or "
                "unexpected response: "
                f"{response.hex(' ')}"
            )

        logger.info(
            "MQTT CONNACK accepted"
        )

        read_timeout = (
            MQTT_KEEP_ALIVE_SECONDS / 2
        )

        ws.settimeout(
            read_timeout
        )

        self._awaiting_pingresp = False
        self._last_pingreq_at = None

        logger.info(
            "MQTT keepalive enabled "
            "keep_alive=%ss read_timeout=%.1fs",
            MQTT_KEEP_ALIVE_SECONDS,
            read_timeout,
        )

    def connect_with_retry(self) -> None:
        delay = self.reconnect_initial_delay

        while True:
            try:
                self.connect()
                return

            except Exception:
                logger.exception(
                    "MQTT connection failed"
                )

                self.close()

                sleep_seconds = self._retry_sleep_seconds(
                    delay
                )

                logger.warning(
                    "Retrying MQTT connection "
                    "in %.2f seconds",
                    sleep_seconds,
                )

                time.sleep(
                    sleep_seconds
                )

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

    def receive_raw(self) -> bytes:
        ws = self._require_connection()

        while True:
            try:
                message = ws.recv()

            except websocket.WebSocketTimeoutException:
                self._handle_read_timeout()
                continue

            except websocket.WebSocketConnectionClosedException as exc:
                raise ConnectionError(
                    "MQTT WebSocket connection closed"
                ) from exc

            except OSError as exc:
                raise ConnectionError(
                    "MQTT WebSocket network error"
                ) from exc

            if message == "":
                raise ConnectionError(
                    "MQTT WebSocket connection "
                    "closed by remote peer"
                )

            if isinstance(message, str):
                raise RuntimeError(
                    "Expected binary MQTT message, "
                    f"received text: {message}"
                )

            return message

    def listen(
        self,
        on_message: Callable[[bytes], None],
    ) -> None:
        logger.info(
            "MQTT listen loop started"
        )

        while True:
            message = self.receive_raw()

            on_message(
                message
            )

    def close(self) -> None:
        if self.websocket is None:
            return

        try:
            self.websocket.close()

        finally:
            self.websocket = None
            self._awaiting_pingresp = False
            self._last_pingreq_at = None

        logger.info(
            "WebSocket closed"
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

        logger.info(
            "Subscribing topic=%s "
            "qos=%s packet_id=%s",
            topic,
            qos,
            packet_id,
        )

        try:
            ws.send_binary(
                packet
            )

            response = ws.recv()

        except websocket.WebSocketConnectionClosedException as exc:
            raise ConnectionError(
                "MQTT WebSocket closed "
                "during SUBSCRIBE"
            ) from exc

        except OSError as exc:
            raise ConnectionError(
                "MQTT WebSocket network error "
                "during SUBSCRIBE"
            ) from exc

        if response == "":
            raise ConnectionError(
                "MQTT WebSocket closed "
                "while waiting for SUBACK"
            )

        if isinstance(response, str):
            raise RuntimeError(
                "Expected binary MQTT SUBACK, "
                f"received text: {response}"
            )

        if not is_successful_suback(
            response,
            expected_packet_id=packet_id,
        ):
            raise RuntimeError(
                "MQTT subscription rejected "
                "or unexpected SUBACK: "
                f"{response.hex(' ')}"
            )

        logger.info(
            "MQTT subscription accepted "
            "topic=%s packet_id=%s",
            topic,
            packet_id,
        )

        return packet_id

    def _reconnect_and_resubscribe(
        self,
    ) -> None:
        delay = self.reconnect_initial_delay

        while True:
            self.close()

            try:
                logger.info(
                    "Attempting MQTT reconnect"
                )

                self.connect()

                self._resubscribe_active_topics()

                logger.info(
                    "MQTT reconnect and "
                    "resubscribe completed"
                )

                return

            except Exception:
                logger.exception(
                    "MQTT reconnect/resubscribe failed"
                )

                self.close()

                sleep_seconds = self._retry_sleep_seconds(
                    delay
                )

                logger.warning(
                    "Retrying MQTT reconnect "
                    "in %.2f seconds",
                    sleep_seconds,
                )

                time.sleep(
                    sleep_seconds
                )

                delay = min(
                    delay * 2,
                    self.reconnect_max_delay,
                )

    def _resubscribe_active_topics(
        self,
    ) -> None:
        if not self._subscriptions:
            logger.info(
                "No active MQTT subscriptions "
                "to restore"
            )
            return

        subscriptions = tuple(
            self._subscriptions.items()
        )

        logger.info(
            "Restoring %s MQTT subscription(s)",
            len(subscriptions),
        )

        for topic, qos in subscriptions:
            self._subscribe_once(
                topic=topic,
                qos=qos,
            )

        logger.info(
            "MQTT subscriptions restored"
        )

    def _handle_read_timeout(
        self,
    ) -> None:
        now = time.monotonic()

        if self._awaiting_pingresp:
            if self._last_pingreq_at is None:
                raise ConnectionError(
                    "MQTT keepalive state is invalid"
                )

            elapsed = (
                now
                - self._last_pingreq_at
            )

            if (
                elapsed
                >= MQTT_KEEP_ALIVE_SECONDS
            ):
                raise ConnectionError(
                    "MQTT PINGRESP timeout"
                )

            logger.debug(
                "Still waiting for MQTT PINGRESP "
                "elapsed=%.1fs",
                elapsed,
            )
            return

        ws = self._require_connection()

        packet = build_pingreq_packet()

        try:
            ws.send_binary(
                packet
            )

        except websocket.WebSocketConnectionClosedException as exc:
            raise ConnectionError(
                "MQTT WebSocket closed "
                "while sending PINGREQ"
            ) from exc

        except OSError as exc:
            raise ConnectionError(
                "MQTT network error "
                "while sending PINGREQ"
            ) from exc

        self._awaiting_pingresp = True
        self._last_pingreq_at = now

        logger.info(
            "MQTT PINGREQ sent"
        )

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
            raise ConnectionError(
                "MQTT WebSocket is not connected"
            )

        return self.websocket

    def _allocate_packet_id(self) -> int:
        packet_id = self._next_packet_id

        self._next_packet_id += 1

        if self._next_packet_id > 65535:
            self._next_packet_id = 1

        return packet_id