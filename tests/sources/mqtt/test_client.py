from unittest.mock import Mock

from mystake.sources.mqtt.client import (
    MystakeMqttClient,
)


def test_successful_subscribe_is_registered(
    monkeypatch,
) -> None:
    client = MystakeMqttClient()

    subscribe_once = Mock(
        return_value=7
    )

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
        raise RuntimeError(
            "subscription failed"
        )

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
        raise AssertionError(
            "Expected RuntimeError"
        )

    assert client._subscriptions == {}


def test_successful_unsubscribe_removes_subscription(
    monkeypatch,
) -> None:
    client = MystakeMqttClient()

    client._subscriptions = {
        "live/gamenew/123": 0,
        "live/gamenew/456": 0,
    }

    unsubscribe_once = Mock(
        return_value=11
    )

    monkeypatch.setattr(
        client,
        "_unsubscribe_once",
        unsubscribe_once,
    )

    packet_id = client.unsubscribe(
        "live/gamenew/123"
    )

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
        raise RuntimeError(
            "unsubscribe failed"
        )

    monkeypatch.setattr(
        client,
        "_unsubscribe_once",
        fail,
    )

    try:
        client.unsubscribe(
            "live/gamenew/123"
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError(
            "Expected RuntimeError"
        )

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

    unsubscribe_once = Mock(
        return_value=15
    )

    monkeypatch.setattr(
        client,
        "_unsubscribe_once",
        unsubscribe_once,
    )

    packet_id = client.unsubscribe(
        "live/gamenew/123"
    )

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

    calls: list[
        tuple[str, int]
    ] = []

    def fake_subscribe_once(
        topic: str,
        qos: int,
    ) -> int:
        calls.append(
            (topic, qos)
        )
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

    client.unsubscribe(
        "live/gamenew/123"
    )

    calls: list[
        tuple[str, int]
    ] = []

    def fake_subscribe_once(
        topic: str,
        qos: int,
    ) -> int:
        calls.append(
            (topic, qos)
        )
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

    publish_packet = (
        b"\x30\x1a"
        b"\x00\x0e"
        b"prematch/games"
        b"cache:test"
    )

    calls = 0

    def fake_receive_raw() -> bytes:
        nonlocal calls

        calls += 1

        if calls == 1:
            raise ConnectionError(
                "test disconnect"
            )

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

def test_unsubscribe_preserves_publish_before_unsuback(
    monkeypatch,
) -> None:
    client = MystakeMqttClient()

    client._subscriptions = {
        "live/gamenew/123": 0,
    }

    publish_packet = (
        b"\x30\x1a"
        b"\x00\x0e"
        b"prematch/games"
        b"cache:test"
    )

    unsuback_packet = (
        b"\xB0\x02\x00\x01"
    )

    ws = Mock()

    ws.recv.side_effect = [
        publish_packet,
        unsuback_packet,
    ]

    client.websocket = ws

    packet_id = client.unsubscribe(
        "live/gamenew/123"
    )

    assert packet_id == 1
    assert client._subscriptions == {}

    message = client.receive_publish()

    assert message.topic == "prematch/games"
    assert message.payload == b"cache:test"