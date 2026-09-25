from __future__ import annotations

from dataclasses import dataclass

from mystake.models.market import Market
from mystake.models.selection import Selection
from mystake.models.snapshot import Snapshot


@dataclass(frozen=True)
class MarketChange:
    market_id: int | str | None


@dataclass(frozen=True)
class SelectionChange:
    selection_id: int | str | None
    market_id: int | str | None


@dataclass(frozen=True)
class SelectionPriceChange:
    selection_id: int | str | None
    market_id: int | str | None
    old: float | None
    new: float | None


@dataclass(frozen=True)
class PrematchSnapshotDiff:
    added_markets: tuple[MarketChange, ...]
    removed_markets: tuple[MarketChange, ...]
    added_selections: tuple[SelectionChange, ...]
    removed_selections: tuple[SelectionChange, ...]
    price_changes: tuple[SelectionPriceChange, ...]

    @property
    def has_changes(self) -> bool:
        return any(
            (
                self.added_markets,
                self.removed_markets,
                self.added_selections,
                self.removed_selections,
                self.price_changes,
            )
        )


def diff_prematch_snapshots(
    previous: Snapshot,
    current: Snapshot,
) -> PrematchSnapshotDiff:
    """
    Semantic market/selection diff between two authoritative
    `getprematchgamefull` snapshots, keyed by stable source ids
    (`Market.id`/`Selection.id`) rather than display names or
    dict/list ordering (AGENTS.md accuracy rule; no market/selection
    name has been observed on this endpoint - see SCHEMA.md section 2).

    A market or selection that is unchanged between the two snapshots
    never appears in the returned diff, regardless of its position in
    either snapshot's underlying dict.
    """
    previous_markets = _index_markets(previous.markets)
    current_markets = _index_markets(current.markets)

    previous_market_ids = set(previous_markets)
    current_market_ids = set(current_markets)

    added_markets = tuple(
        MarketChange(market_id=market_id)
        for market_id in sorted(
            current_market_ids - previous_market_ids,
            key=str,
        )
    )

    removed_markets = tuple(
        MarketChange(market_id=market_id)
        for market_id in sorted(
            previous_market_ids - current_market_ids,
            key=str,
        )
    )

    previous_selections = _index_selections(previous.markets)
    current_selections = _index_selections(current.markets)

    previous_selection_ids = set(previous_selections)
    current_selection_ids = set(current_selections)

    added_selections = tuple(
        SelectionChange(
            selection_id=selection_id,
            market_id=current_selections[selection_id].market_id,
        )
        for selection_id in sorted(
            current_selection_ids - previous_selection_ids,
            key=str,
        )
    )

    removed_selections = tuple(
        SelectionChange(
            selection_id=selection_id,
            market_id=previous_selections[selection_id].market_id,
        )
        for selection_id in sorted(
            previous_selection_ids - current_selection_ids,
            key=str,
        )
    )

    price_changes = []

    for selection_id in sorted(
        previous_selection_ids & current_selection_ids,
        key=str,
    ):
        old_selection = previous_selections[selection_id]
        new_selection = current_selections[selection_id]

        if old_selection.price != new_selection.price:
            price_changes.append(
                SelectionPriceChange(
                    selection_id=selection_id,
                    market_id=new_selection.market_id,
                    old=old_selection.price,
                    new=new_selection.price,
                )
            )

    return PrematchSnapshotDiff(
        added_markets=added_markets,
        removed_markets=removed_markets,
        added_selections=added_selections,
        removed_selections=removed_selections,
        price_changes=tuple(price_changes),
    )


def _index_markets(
    markets: tuple[Market, ...],
) -> dict[int | str | None, Market]:
    return {market.id: market for market in markets if market.id is not None}


def _index_selections(
    markets: tuple[Market, ...],
) -> dict[int | str | None, Selection]:
    result: dict[int | str | None, Selection] = {}

    for market in markets:
        for selection in market.selections:
            if selection.id is not None:
                result[selection.id] = selection

    return result
