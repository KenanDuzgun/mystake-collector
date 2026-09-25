from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mystake.models._coerce import coerce_bool, coerce_float


@dataclass(frozen=True)
class Selection:
    """
    A single betting selection (outcome) within a market.

    `raw` always holds the complete, untouched source dict for this
    selection so that fields not yet modeled are never lost.
    """

    id: int | str | None
    market_id: int | str | None
    price: float | None
    visible: bool | None
    locked: bool | None
    raw: dict[str, Any]


def parse_prematch_selection(
    selection_id: int | str,
    data: dict[str, Any],
    *,
    market_id: int | str | None,
) -> Selection:
    """
    Parse a selection from a prematch `ev` market entry.

    Observed fields (see docs/product/SCHEMA.md): `coef` (price),
    `lock` (locked). No `visible` field has been observed on
    prematch selections.
    """
    return Selection(
        id=selection_id,
        market_id=market_id,
        price=coerce_float(data.get("coef")),
        visible=None,
        locked=coerce_bool(data.get("lock")),
        raw=data,
    )


def parse_live_selection(
    data: dict[str, Any],
) -> Selection:
    """
    Parse a selection from a live `gmk` entry.

    Observed fields: `id`, `mid` (market id), `v` (price), `visible`.
    No `lock` field has been observed on live selections.
    """
    return Selection(
        id=data.get("id"),
        market_id=data.get("mid"),
        price=coerce_float(data.get("v")),
        visible=coerce_bool(data.get("visible")),
        locked=None,
        raw=data,
    )
