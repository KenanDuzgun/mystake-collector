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

    STRONG EVIDENCE (this session): the decoded payload is a dict with
    top-level `Games` / `Sports` / `Regions` / `Championats` / `Teams`
    / `mk` keys.

    Only `Games` is parsed here, one `Fixture` per entry. Per
    AGENTS.md's "do not invent field mappings" rule, this does not
    attempt to join `Games` entries against the `Sports` / `Regions`
    / `Championats` / `Teams` lookup lists - no verified payload
    sample exists yet to confirm the cross-reference key names, and a
    wrong guess would silently corrupt fixture metadata. `mk` is
    UNKNOWN (same status as the `mk` field on individual live game
    snapshots, see docs/product/SCHEMA.md) and is not parsed.
    """
    if not isinstance(payload, dict) or "Games" not in payload:
        raise ValueError(
            "live/headernew/en response has no 'Games' key - "
            "cannot distinguish a malformed/unexpected response "
            "from a legitimately empty one"
        )

    games = payload.get("Games")

    return tuple(parse_fixture_from_live_game_item(item) for item in _iter_dicts(games))


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
