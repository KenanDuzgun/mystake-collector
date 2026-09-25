from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Fixture:
    """
    A discovery-level fixture entry, covering any sport MyStake
    exposes, sourced from either the prematch (`getheader/en`
    `GameSmallItem`) or live (`live/headernew/en` `Games[]` entry)
    discovery hierarchy.

    `raw` always holds the complete, untouched source dict for this
    fixture so fields not yet modeled are never lost.

    `sport_id` / `region_id` / `champ_id` are populated by the
    hierarchy-walking parser (see `mystake.pipeline.prematch_header_parser`)
    from the parent `Sport`/`Region`/`Champ` node, not from the item
    itself. Their exact key names (`ID`/`Name` on those parent nodes)
    are HYPOTHESIS (see docs/product/SCHEMA.md) - inferred from the
    `GameSmallItem.ID` convention, not yet independently verified.
    """

    game_id: int | str | None
    sport: Any
    region: Any
    champ: Any
    start_time: Any
    team1: Any
    team2: Any
    raw: dict[str, Any]
    source: str = "prematch_getheader"
    sport_id: Any = None
    region_id: Any = None
    champ_id: Any = None


def parse_fixture_from_getheader_item(
    item: dict[str, Any],
) -> Fixture:
    """
    Parse a `GameSmallItem` entry observed under
    `getheader/en` -> Sports -> Regions -> Champs -> GameSmallItems.

    The fixture identifier field is `ID` (PROVEN this session), not
    `GameId`. `GameId` is kept as a fallback for resilience only; it
    has not been observed on `GameSmallItem` itself (it is however the
    field name used by the unrelated `prematch/games` UpdateList
    payload - see docs/product/SCHEMA.md).
    """
    game_id = item.get("ID")

    if game_id is None:
        game_id = item.get("GameId")

    return Fixture(
        game_id=game_id,
        sport=item.get("Sport"),
        region=item.get("Region"),
        champ=item.get("Champ"),
        start_time=item.get("StartTime"),
        team1=item.get("t1"),
        team2=item.get("t2"),
        raw=item,
    )


def parse_fixture_from_live_game_item(
    item: dict[str, Any],
) -> Fixture:
    """
    Parse a single entry from `live/headernew/en`'s `Games` list.

    STRONG EVIDENCE: `live/headernew/en` decodes to a payload with
    top-level `Games` / `Sports` / `Regions` / `Championats` / `Teams`
    / `mk` keys (see docs/product/SCHEMA.md). The exact field layout
    of a `Games` entry, and how (or whether) it cross-references the
    `Sports`/`Regions`/`Championats`/`Teams` lookup lists, is UNKNOWN
    -- no live payload sample has been captured/verified yet.

    Per AGENTS.md, unverified field mappings must not be invented.
    This parser therefore only reuses the one field-naming convention
    already proven for this API family (`ID` as the item's own
    identifier, see `parse_fixture_from_getheader_item`) and otherwise
    leaves sport/region/champ/team fields unresolved (`None`) rather
    than guessing cross-reference key names. The full entry is always
    preserved via `raw`, so no data is lost while this remains
    unresolved. If a `Games` entry happens to carry the same
    `Sport`/`Region`/`Champ`/`t1`/`t2` keys as a prematch
    `GameSmallItem`, they are picked up here too, but this has not
    been verified against a captured live payload.
    """
    game_id = item.get("ID")

    if game_id is None:
        game_id = item.get("GameId")

    return Fixture(
        game_id=game_id,
        sport=item.get("Sport"),
        region=item.get("Region"),
        champ=item.get("Champ"),
        start_time=item.get("StartTime"),
        team1=item.get("t1"),
        team2=item.get("t2"),
        raw=item,
        source="live_headernew",
    )
