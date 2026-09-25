from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mystake.models.market import parse_live_markets


@dataclass(frozen=True)
class SelectionMetadata:
    """
    Verified display metadata for one live selection, joined from a
    single live snapshot's `mk` (market name/IsHandicap) and `gmk`
    (selection name/line) - see docs/product/SCHEMA.md section 3.
    """

    market_id: int | str | None
    market_name: str | None
    selection_name: str | None
    line: float | None


def build_selection_metadata_lookup(
    snapshot: dict[str, Any],
) -> dict[int | str, SelectionMetadata]:
    """
    Builds a `selection_id -> SelectionMetadata` lookup from one raw
    live snapshot dict, for enriching `LiveSnapshotDiff`'s
    `SelectionPriceChange` entries (which only carry `selection_id`/
    `market_id`) with real market/selection names and line values for
    display. Reuses the existing typed `parse_live_markets` join
    rather than re-deriving market/selection semantics; does not
    change the diff algorithm, dispatcher, or MQTT subscriptions.

    Isolated per call - always built from a single game's own
    snapshot, never shared across GameIds.
    """
    gmk = snapshot.get("gmk")

    if gmk is None:
        return {}

    markets = parse_live_markets(gmk, snapshot.get("mk"))

    lookup: dict[int | str, SelectionMetadata] = {}

    for market in markets:
        for selection in market.selections:
            if selection.id is None:
                continue

            lookup[selection.id] = SelectionMetadata(
                market_id=market.id,
                market_name=market.name,
                selection_name=selection.name,
                line=selection.line,
            )

    return lookup
