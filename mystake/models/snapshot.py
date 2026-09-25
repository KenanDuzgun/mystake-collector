from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mystake.models.market import (
    Market,
    parse_live_markets,
    parse_prematch_markets,
)


@dataclass(frozen=True)
class Snapshot:
    """
    A full game state snapshot from either the prematch or live plane.

    `raw` always holds the complete, untouched source payload this
    snapshot was built from (the decoded `game` dict for prematch, or
    the decoded live cache payload for live), so no unknown field is
    ever lost between the raw MyStake payload and this model.
    """

    game_id: int | str | None
    source: str
    markets: tuple[Market, ...]
    raw: dict[str, Any]


def parse_prematch_snapshot(
    game: dict[str, Any],
) -> Snapshot:
    """
    Parse an authoritative `getprematchgamefull` `game` object.
    """
    ev = game.get("ev")

    markets = (
        parse_prematch_markets(ev)
        if isinstance(ev, dict)
        else ()
    )

    return Snapshot(
        game_id=game.get("id"),
        source="prematch_gamefull",
        markets=markets,
        raw=game,
    )


def parse_live_snapshot(
    data: dict[str, Any],
) -> Snapshot:
    """
    Parse a decoded `live/gamenew/{GameId}` cache payload.
    """
    match = data.get("Match")

    game_id = (
        match.get("GameID")
        if isinstance(match, dict)
        else None
    )

    gmk = data.get("gmk")

    markets = (
        parse_live_markets(gmk)
        if gmk is not None
        else ()
    )

    return Snapshot(
        game_id=game_id,
        source="live",
        markets=markets,
        raw=data,
    )
