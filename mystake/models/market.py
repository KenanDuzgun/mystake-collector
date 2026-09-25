from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mystake.models._coerce import coerce_bool, coerce_int, coerce_str, iter_dict_items
from mystake.models.selection import (
    Selection,
    parse_live_selection,
    parse_prematch_selection,
)


@dataclass(frozen=True)
class Market:
    """
    A betting market grouping its selections.

    `raw` always holds the complete, untouched source value this
    market was built from.
    """

    id: int | str | None
    selections: tuple[Selection, ...]
    name: str | None
    is_handicap: bool | None
    column_count: int | None
    raw: Any


def parse_prematch_markets(
    ev: dict[str, Any],
) -> tuple[Market, ...]:
    """
    Parse the prematch `ev` map: {market_id: {selection_id: {...}}}.

    No market-metadata structure analogous to live's top-level `mk`
    has been observed alongside prematch `ev`, so `name`/`is_handicap`/
    `column_count` are `None` here (see `parse_live_markets`).
    """
    markets: list[Market] = []

    for market_id, selections in ev.items():
        if not isinstance(selections, dict):
            continue

        parsed_selections = tuple(
            parse_prematch_selection(
                selection_id,
                selection_data,
                market_id=market_id,
            )
            for selection_id, selection_data in selections.items()
            if isinstance(selection_data, dict)
        )

        markets.append(
            Market(
                id=market_id,
                selections=parsed_selections,
                name=None,
                is_handicap=None,
                column_count=None,
                raw=selections,
            )
        )

    return tuple(markets)


def _index_market_metadata(
    mk: Any,
) -> dict[int | str, dict[str, Any]]:
    """
    Index a live snapshot's top-level `mk` list by its `ID` field, the
    verified join key into `gmk[].mid` (see docs/product/SCHEMA.md
    section 3: every `mk[].ID` observed matched a `gmk[].mid` in the
    same real snapshot).
    """
    metadata: dict[int | str, dict[str, Any]] = {}

    for entry in iter_dict_items(mk):
        market_id = entry.get("ID")

        if market_id is None:
            continue

        metadata[market_id] = entry

    return metadata


def parse_live_markets(
    gmk: Any,
    mk: Any = None,
) -> tuple[Market, ...]:
    """
    Parse the live `gmk` selection list/dict into markets grouped by
    each selection's `mid` (market id), since live snapshots carry a
    flat selection list rather than a market-keyed structure.

    `mk` (a live snapshot's top-level market-metadata list) is joined
    in by `mk[].ID == gmk[].mid` to expose each market's real `Name`,
    `IsHandicap`, and `ColumnCount` - VERIFIED fields, see
    docs/product/SCHEMA.md section 3. A market whose id has no `mk`
    entry (or when `mk` is omitted/empty) gets `None` for all three
    rather than a fabricated value.
    """
    selections_by_market: dict[
        int | str | None,
        list[Selection],
    ] = {}

    for item in iter_dict_items(gmk):
        selection = parse_live_selection(item)

        selections_by_market.setdefault(
            selection.market_id,
            [],
        ).append(selection)

    market_metadata = _index_market_metadata(mk)

    markets: list[Market] = []

    for market_id, selections in selections_by_market.items():
        metadata = market_metadata.get(market_id) if market_id is not None else None

        markets.append(
            Market(
                id=market_id,
                selections=tuple(selections),
                name=coerce_str(metadata.get("Name")) if metadata is not None else None,
                is_handicap=coerce_bool(metadata.get("IsHandicap"))
                if metadata is not None
                else None,
                column_count=coerce_int(metadata.get("ColumnCount"))
                if metadata is not None
                else None,
                raw=[selection.raw for selection in selections],
            )
        )

    return tuple(markets)
