from __future__ import annotations

from decimal import Decimal
from typing import Any


def coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float, Decimal)):
        return float(value)

    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None

    return None


def coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value

    return None


def iter_dict_items(value: Any):
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item
        return

    if isinstance(value, dict):
        for item in value.values():
            if isinstance(item, dict):
                yield item
