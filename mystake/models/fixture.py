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
    team1_id: Any = None
    team2_id: Any = None


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
    *,
    sports_by_id: dict[Any, dict[str, Any]] | None = None,
    regions_by_id: dict[Any, dict[str, Any]] | None = None,
    champs_by_id: dict[Any, dict[str, Any]] | None = None,
    teams_by_id: dict[Any, dict[str, Any]] | None = None,
) -> Fixture:
    """
    Parse a single entry from `live/headernew/en`'s `Games` list.

    PROVEN (this session, verified against
    `tests/fixtures/mystake-live-header-sanitized.json`): a `Games`
    entry's `Sport` / `Region` / `Champ` / `Team1` / `Team2` fields are
    foreign-key ids into the payload's top-level `Sports` / `Regions`
    / `Championats` / `Teams` lookup lists (each a list of
    `{"ID": ..., "Name": ...}` records), not embedded names. All 84
    captured `Games` entries resolve cleanly through these five
    relationships. `parse_live_header` builds the `*_by_id` lookup
    maps once per payload and passes them in here.

    If a lookup id has no matching entry (not observed in the capture,
    but not guaranteed for all future payloads), the id itself is kept
    as the resolved value instead of a name - this is honest
    (distinguishable from a real name) and never drops the fixture.
    When no lookup map is supplied at all (e.g. direct unit-test calls,
    or a hypothetical entry that already carries names the way a
    prematch `GameSmallItem` does), the raw field value is used as-is,
    which also keeps this function's older behavior for entries with
    string `Sport`/`Region`/`Champ` values.

    The full entry is always preserved via `raw`, so no data is lost
    regardless of lookup resolution.
    """
    game_id = item.get("ID")

    if game_id is None:
        game_id = item.get("GameId")

    sport_id = item.get("Sport")
    region_id = item.get("Region")
    champ_id = item.get("Champ")
    team1_id = item.get("Team1", item.get("t1"))
    team2_id = item.get("Team2", item.get("t2"))

    return Fixture(
        game_id=game_id,
        sport=_resolve_name(sport_id, sports_by_id),
        region=_resolve_name(region_id, regions_by_id),
        champ=_resolve_name(champ_id, champs_by_id),
        start_time=item.get("StartTime"),
        team1=_resolve_name(team1_id, teams_by_id),
        team2=_resolve_name(team2_id, teams_by_id),
        raw=item,
        source="live_headernew",
        sport_id=sport_id,
        region_id=region_id,
        champ_id=champ_id,
        team1_id=team1_id,
        team2_id=team2_id,
    )


def _resolve_name(
    lookup_id: Any,
    lookup_by_id: dict[Any, dict[str, Any]] | None,
) -> Any:
    """
    Resolve `lookup_id` to a `Name` via `lookup_by_id`, falling back to
    `lookup_id` itself (never `None`, and never a guessed name) when
    there is no lookup map or no matching entry.
    """
    if lookup_by_id is None:
        return lookup_id

    entry = lookup_by_id.get(lookup_id)

    if entry is None:
        return lookup_id

    name = entry.get("Name")

    return name if name is not None else lookup_id
