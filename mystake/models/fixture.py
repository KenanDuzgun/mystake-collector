from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Fixture:
    """
    A discovery-level fixture entry (a `GameSmallItem` from
    `getheader/en`), covering any sport MyStake exposes.

    `raw` always holds the complete, untouched source dict for this
    fixture so fields not yet modeled are never lost.
    """

    game_id: int | str | None
    sport: Any
    region: Any
    champ: Any
    start_time: Any
    team1: Any
    team2: Any
    raw: dict[str, Any]


def parse_fixture_from_getheader_item(
    item: dict[str, Any],
) -> Fixture:
    """
    Parse a `GameSmallItem` entry observed under
    `getheader/en` -> Sports -> Regions -> Champs -> GameSmallItems.
    """
    return Fixture(
        game_id=item.get("GameId"),
        sport=item.get("Sport"),
        region=item.get("Region"),
        champ=item.get("Champ"),
        start_time=item.get("StartTime"),
        team1=item.get("t1"),
        team2=item.get("t2"),
        raw=item,
    )
