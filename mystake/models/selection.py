from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mystake.models._coerce import coerce_bool, coerce_float, coerce_str


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
    name: str | None
    line: float | None
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
    prematch selections, and no selection-name/line field has been
    observed on prematch selections either (unlike live `gmk`'s `pn`/
    `h` - see `parse_live_selection`), so `name`/`line` are `None`.
    """
    return Selection(
        id=selection_id,
        market_id=market_id,
        price=coerce_float(data.get("coef")),
        visible=None,
        locked=coerce_bool(data.get("lock")),
        name=None,
        line=None,
        raw=data,
    )


def parse_live_selection(
    data: dict[str, Any],
) -> Selection:
    """
    Parse a selection from a live `gmk` entry.

    Observed fields: `id`, `mid` (market id), `v` (price), `visible`.
    No `lock` field has been observed on live selections.

    `pn` (e.g. `"under"`, `"1"`, `"2:0"`) is a real human-readable
    selection/outcome name and `h` (e.g. `2.5`) a real handicap/line
    value - both VERIFIED against real `live/gamenew/{GameId}`
    payloads (see docs/product/SCHEMA.md section 3). Both are optional
    per entry (e.g. 3-way markets carry no `h`); missing metadata
    yields `None` rather than a fabricated value.
    """
    return Selection(
        id=data.get("id"),
        market_id=data.get("mid"),
        price=coerce_float(data.get("v")),
        visible=coerce_bool(data.get("visible")),
        locked=None,
        name=coerce_str(data.get("pn")),
        line=coerce_float(data.get("h")),
        raw=data,
    )
