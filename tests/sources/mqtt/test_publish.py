from mystake.sources.mqtt.protocol import (
    parse_publish_packet,
)


def test_parse_qos_zero_publish() -> None:
    topic = "prematch/games"

    payload = (
        b"cache:https://example.com/cache"
    )

    variable_header = (
        len(topic).to_bytes(
            2,
            byteorder="big",
        )
        + topic.encode("utf-8")
    )

    remaining_length = (
        len(variable_header)
        + len(payload)
    )

    assert remaining_length < 128

    packet = (
        bytes([
            0x30,
            remaining_length,
        ])
        + variable_header
        + payload
    )

    message = parse_publish_packet(
        packet
    )

    assert message.topic == "prematch/games"
    assert message.payload == payload
    assert message.qos == 0
    assert message.packet_id is None
    assert message.retain is False
    assert message.dup is False