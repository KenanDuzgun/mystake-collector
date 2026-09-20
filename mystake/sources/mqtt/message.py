from dataclasses import dataclass


@dataclass(frozen=True)
class MqttPublishMessage:
    topic: str
    payload: bytes
    qos: int
    retain: bool
    dup: bool
    packet_id: int | None