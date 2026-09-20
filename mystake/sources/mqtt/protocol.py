import time

from mystake.config import (
    MQTT_CLEAN_SESSION,
    MQTT_KEEP_ALIVE_SECONDS,
    MQTT_PROTOCOL_LEVEL,
    MQTT_PROTOCOL_NAME,
)
from mystake.sources.mqtt.message import MqttPublishMessage


def create_client_id() -> str:
    """
    Create a client ID using the same 13-digit millisecond timestamp
    format observed in the browser MQTT CONNECT packet.
    """
    return str(int(time.time() * 1000))


def encode_utf8(value: str) -> bytes:
    """
    Encode a UTF-8 string using the MQTT length-prefixed format.
    """
    encoded = value.encode("utf-8")
    length = len(encoded).to_bytes(2, byteorder="big")

    return length + encoded


def encode_remaining_length(length: int) -> bytes:
    """
    Encode MQTT Remaining Length using MQTT variable-byte encoding.
    """
    encoded = bytearray()

    while True:
        digit = length % 128
        length //= 128

        if length > 0:
            digit |= 0x80

        encoded.append(digit)

        if length == 0:
            break

    return bytes(encoded)


def build_connect_packet(client_id: str) -> bytes:
    """
    Build MQTT 3.1.1 CONNECT packet.
    """
    connect_flags = 0

    if MQTT_CLEAN_SESSION:
        connect_flags |= 0x02

    variable_header = (
        encode_utf8(MQTT_PROTOCOL_NAME)
        + bytes([MQTT_PROTOCOL_LEVEL])
        + bytes([connect_flags])
        + MQTT_KEEP_ALIVE_SECONDS.to_bytes(
            2,
            byteorder="big",
        )
    )

    payload = encode_utf8(client_id)

    remaining_length = (
        len(variable_header)
        + len(payload)
    )

    fixed_header = (
        bytes([0x10])
        + encode_remaining_length(remaining_length)
    )

    return (
        fixed_header
        + variable_header
        + payload
    )


def is_successful_connack(packet: bytes) -> bool:
    """
    Return True when broker accepts the MQTT connection.
    """
    return packet == b"\x20\x02\x00\x00"


def build_pingreq_packet() -> bytes:
    """
    Build MQTT 3.1.1 PINGREQ packet.
    """
    return b"\xC0\x00"


def is_pingresp(packet: bytes) -> bool:
    """
    Return True when packet is MQTT 3.1.1 PINGRESP.
    """
    return packet == b"\xD0\x00"


def build_subscribe_packet(
    topic: str,
    packet_id: int = 1,
    qos: int = 0,
) -> bytes:
    """
    Build MQTT 3.1.1 SUBSCRIBE packet for a single topic.
    """
    if not 1 <= packet_id <= 65535:
        raise ValueError(
            "packet_id must be between 1 and 65535"
        )

    if qos not in (0, 1, 2):
        raise ValueError(
            "qos must be 0, 1, or 2"
        )

    variable_header = packet_id.to_bytes(
        2,
        byteorder="big",
    )

    payload = (
        encode_utf8(topic)
        + bytes([qos])
    )

    remaining_length = (
        len(variable_header)
        + len(payload)
    )

    fixed_header = (
        bytes([0x82])
        + encode_remaining_length(remaining_length)
    )

    return (
        fixed_header
        + variable_header
        + payload
    )


def parse_suback(packet: bytes) -> tuple[int, list[int]]:
    """
    Parse a simple MQTT SUBACK packet.

    Returns:
        (packet_id, return_codes)
    """
    if len(packet) < 5:
        raise ValueError(
            "SUBACK packet is too short"
        )

    if packet[0] != 0x90:
        raise ValueError(
            f"Expected SUBACK (0x90), got 0x{packet[0]:02x}"
        )

    remaining_length = packet[1]

    if remaining_length != len(packet) - 2:
        raise ValueError(
            "Unexpected SUBACK remaining length"
        )

    packet_id = int.from_bytes(
        packet[2:4],
        byteorder="big",
    )

    return_codes = list(
        packet[4:]
    )

    return packet_id, return_codes


def is_successful_suback(
    packet: bytes,
    expected_packet_id: int,
) -> bool:
    """
    Return True when SUBACK confirms the requested subscription.
    """
    packet_id, return_codes = parse_suback(packet)

    if packet_id != expected_packet_id:
        return False

    if not return_codes:
        return False

    return all(
        code in (0x00, 0x01, 0x02)
        for code in return_codes
    )


def build_unsubscribe_packet(
    topic: str,
    packet_id: int = 1,
) -> bytes:
    """
    Build MQTT 3.1.1 UNSUBSCRIBE packet for a single topic.
    """
    if not 1 <= packet_id <= 65535:
        raise ValueError(
            "packet_id must be between 1 and 65535"
        )

    variable_header = packet_id.to_bytes(
        2,
        byteorder="big",
    )

    payload = encode_utf8(topic)

    remaining_length = (
        len(variable_header)
        + len(payload)
    )

    fixed_header = (
        bytes([0xA2])
        + encode_remaining_length(remaining_length)
    )

    return (
        fixed_header
        + variable_header
        + payload
    )


def is_successful_unsuback(
    packet: bytes,
    expected_packet_id: int,
) -> bool:
    """
    Return True when MQTT UNSUBACK confirms the requested unsubscribe.
    """
    if len(packet) != 4:
        return False

    if packet[0] != 0xB0:
        return False

    if packet[1] != 0x02:
        return False

    packet_id = int.from_bytes(
        packet[2:4],
        byteorder="big",
    )

    return packet_id == expected_packet_id


def decode_remaining_length(
    packet: bytes,
    offset: int = 1,
) -> tuple[int, int]:
    """
    Decode MQTT variable-byte Remaining Length.

    Returns:
        (remaining_length, bytes_consumed)
    """
    multiplier = 1
    value = 0
    consumed = 0

    while True:
        if offset + consumed >= len(packet):
            raise ValueError(
                "Incomplete MQTT Remaining Length"
            )

        encoded_byte = packet[offset + consumed]

        value += (
            encoded_byte & 0x7F
        ) * multiplier

        consumed += 1

        if encoded_byte & 0x80 == 0:
            break

        multiplier *= 128

        if multiplier > 128 * 128 * 128:
            raise ValueError(
                "Malformed MQTT Remaining Length"
            )

    return value, consumed


def parse_publish_packet(
    packet: bytes,
) -> MqttPublishMessage:
    """
    Parse an MQTT 3.1.1 PUBLISH packet.
    """
    if not packet:
        raise ValueError(
            "Empty MQTT packet"
        )

    first_byte = packet[0]

    packet_type = (
        first_byte >> 4
    )

    if packet_type != 3:
        raise ValueError(
            f"Expected PUBLISH packet, got type={packet_type}"
        )

    dup = bool(
        first_byte & 0x08
    )

    qos = (
        first_byte >> 1
    ) & 0x03

    retain = bool(
        first_byte & 0x01
    )

    if qos == 3:
        raise ValueError(
            "Invalid MQTT PUBLISH QoS value"
        )

    remaining_length, remaining_length_bytes = (
        decode_remaining_length(
            packet,
            offset=1,
        )
    )

    index = (
        1
        + remaining_length_bytes
    )

    expected_total_length = (
        index
        + remaining_length
    )

    if len(packet) != expected_total_length:
        raise ValueError(
            "MQTT packet length does not match Remaining Length"
        )

    if index + 2 > len(packet):
        raise ValueError(
            "Missing PUBLISH topic length"
        )

    topic_length = int.from_bytes(
        packet[index:index + 2],
        byteorder="big",
    )

    index += 2

    topic_end = (
        index
        + topic_length
    )

    if topic_end > len(packet):
        raise ValueError(
            "Invalid PUBLISH topic length"
        )

    topic = packet[
        index:topic_end
    ].decode("utf-8")

    index = topic_end

    packet_id = None

    if qos > 0:
        if index + 2 > len(packet):
            raise ValueError(
                "Missing PUBLISH packet identifier"
            )

        packet_id = int.from_bytes(
            packet[index:index + 2],
            byteorder="big",
        )

        index += 2

    payload = packet[index:]

    return MqttPublishMessage(
        topic=topic,
        payload=payload,
        qos=qos,
        retain=retain,
        dup=dup,
        packet_id=packet_id,
    )