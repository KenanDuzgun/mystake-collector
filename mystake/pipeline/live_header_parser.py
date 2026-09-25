from __future__ import annotations

from typing import Any

from mystake.models.fixture import (
    Fixture,
    parse_fixture_from_live_game_item,
)


def parse_live_header(
    payload: Any,
) -> tuple[Fixture, ...]:
    """
    Parse a decoded `live/headernew/en` cache payload into a flat
    tuple of `Fixture` objects.

    PROVEN (this session, verified against
    `tests/fixtures/mystake-live-header-sanitized.json`): the decoded
    payload is a dict with top-level `Games` / `Sports` / `Regions` /
    `Championats` / `Teams` / `mk` keys. `Games[].Sport` / `.Region` /
    `.Champ` / `.Team1` / `.Team2` are foreign-key ids resolved against
    the corresponding top-level lookup list (each a list of
    `{"ID": ..., "Name": ...}` records) - see
    `mystake.models.fixture.parse_fixture_from_live_game_item`. `mk` is
    UNKNOWN (same status as the `mk` field on individual live game
    snapshots, see docs/product/SCHEMA.md) and is not parsed; the
    capture used to verify this schema has an empty `mk` array, so its
    schema remains unobserved.
    """
    if not isinstance(payload, dict) or "Games" not in payload:
        raise ValueError(
            "live/headernew/en response has no 'Games' key - "
            "cannot distinguish a malformed/unexpected response "
            "from a legitimately empty one"
        )

    games = payload.get("Games")

    sports_by_id = _index_by_id(payload.get("Sports"))
    regions_by_id = _index_by_id(payload.get("Regions"))
    champs_by_id = _index_by_id(payload.get("Championats"))
    teams_by_id = _index_by_id(payload.get("Teams"))

    return tuple(
        parse_fixture_from_live_game_item(
            item,
            sports_by_id=sports_by_id,
            regions_by_id=regions_by_id,
            champs_by_id=champs_by_id,
            teams_by_id=teams_by_id,
        )
        for item in _iter_dicts(games)
    )


def _index_by_id(value: Any) -> dict[Any, dict[str, Any]]:
    """
    Index a lookup list (e.g. top-level `Sports`) by its `ID` field.
    Entries missing an `ID`, or a payload where the lookup list itself
    is absent/malformed, resolve to an empty map - callers then fall
    back to the raw foreign-key id (see `_resolve_name`), never a
    guessed name.
    """
    indexed: dict[Any, dict[str, Any]] = {}

    for entry in _iter_dicts(value):
        entry_id = entry.get("ID")

        if entry_id is not None:
            indexed[entry_id] = entry

    return indexed


def _iter_dicts(value: Any):
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item
        return

    if isinstance(value, dict):
        for item in value.values():
            if isinstance(item, dict):
                yield item
