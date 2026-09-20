from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class LiveEventType(StrEnum):
    CLOCK_CHANGED = "CLOCK_CHANGED"
    SCORE_CHANGED = "SCORE_CHANGED"
    CORNER_CHANGED = "CORNER_CHANGED"
    BET_STATUS_CHANGED = "BET_STATUS_CHANGED"

    PRICE_CHANGED = "PRICE_CHANGED"

    SELECTION_VISIBLE = "SELECTION_VISIBLE"
    SELECTION_HIDDEN = "SELECTION_HIDDEN"

    TIMELINE_EVENT_ADDED = "TIMELINE_EVENT_ADDED"


@dataclass(frozen=True)
class LiveDomainEvent:
    event_type: LiveEventType
    game_id: int | str | None
    payload: dict[str, Any]