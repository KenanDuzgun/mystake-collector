import pytest

from mystake.sources.mqtt.protocol import (
    build_connect_packet,
    build_pingreq_packet,
    build_unsubscribe_packet,
    encode_remaining_length,
    is_pingresp,
    is_successful_connack,
    is_successful_unsuback,
)


def test_encode_remaining_length_small_value() -> None:
    assert encode_remaining_length(25) == b"\x19"


def test_encode_remaining_length_multi_byte() -> None:
    assert encode_remaining_length(128) == b"\x80\x01"


def test_build_connect_packet_matches_browser_structure() -> None:
    client_id = "1789928015273"

    packet = build_connect_packet(client_id)

    expected = bytes.fromhex(
        "10 19 "
        "00 04 4d 51 54 54 "
        "04 "
        "02 "
        "00 3c "
        "00 0d "
        "31 37 38 39 39 32 38 30 31 35 32 37 33"
    )

    assert packet == expected


def test_successful_connack() -> None:
    assert is_successful_connack(
        b"\x20\x02\x00\x00"
    )


def test_rejected_connack_is_not_successful() -> None:
    assert not is_successful_connack(
        b"\x20\x02\x00\x05"
    )


def test_build_pingreq_packet() -> None:
    assert build_pingreq_packet() == b"\xC0\x00"


def test_pingresp_detection() -> None:
    assert is_pingresp(
        b"\xD0\x00"
    )

    assert not is_pingresp(
        b"\xC0\x00"
    )


def test_build_unsubscribe_packet() -> None:
    packet = build_unsubscribe_packet(
        topic="live/gamenew/73689937",
        packet_id=2,
    )

    expected = bytes.fromhex(
        "A2 19 "
        "00 02 "
        "00 15 "
        "6C 69 76 65 2F 67 61 6D 65 6E 65 77 2F "
        "37 33 36 38 39 39 33 37"
    )

    assert packet == expected


def test_build_unsubscribe_packet_rejects_zero_packet_id() -> None:
    with pytest.raises(
        ValueError,
        match="packet_id must be between 1 and 65535",
    ):
        build_unsubscribe_packet(
            topic="live/gamenew/73689937",
            packet_id=0,
        )


def test_build_unsubscribe_packet_rejects_too_large_packet_id() -> None:
    with pytest.raises(
        ValueError,
        match="packet_id must be between 1 and 65535",
    ):
        build_unsubscribe_packet(
            topic="live/gamenew/73689937",
            packet_id=65536,
        )


def test_successful_unsuback() -> None:
    assert is_successful_unsuback(
        packet=b"\xB0\x02\x00\x02",
        expected_packet_id=2,
    )


def test_unsuback_wrong_packet_id_is_not_successful() -> None:
    assert not is_successful_unsuback(
        packet=b"\xB0\x02\x00\x03",
        expected_packet_id=2,
    )


def test_unsuback_wrong_packet_type_is_not_successful() -> None:
    assert not is_successful_unsuback(
        packet=b"\x90\x02\x00\x02",
        expected_packet_id=2,
    )


def test_unsuback_wrong_remaining_length_is_not_successful() -> None:
    assert not is_successful_unsuback(
        packet=b"\xB0\x03\x00\x02",
        expected_packet_id=2,
    )