from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mystake.models._coerce import iter_dict_items
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
    raw: Any


def parse_prematch_markets(
    ev: dict[str, Any],
) -> tuple[Market, ...]:
    """
    Parse the prematch `ev` map: {market_id: {selection_id: {...}}}.
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
                raw=selections,
            )
        )

    return tuple(markets)


def parse_live_markets(
    gmk: Any,
) -> tuple[Market, ...]:
    """
    Parse the live `gmk` selection list/dict into markets grouped by
    each selection's `mid` (market id), since live snapshots carry a
    flat selection list rather than a market-keyed structure.
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

    return tuple(
        Market(
            id=market_id,
            selections=tuple(selections),
            raw=[
                selection.raw
                for selection in selections
            ],
        )
        for market_id, selections in selections_by_market.items()
    )
