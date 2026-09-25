from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from mystake.models.fixture import (
    Fixture,
    parse_fixture_from_getheader_item,
)


def parse_prematch_header(
    payload: Any,
) -> tuple[Fixture, ...]:
    """
    Parse a decoded `getheader/en` response into a flat tuple of
    `Fixture` objects, across every sport/region/championship.

    Observed hierarchy (PROVEN, verified against a real
    `getheader/en` response this session):

        {"EN": {"Sports": {<id>: {...}}}}
        Sports -> Regions -> Champs -> GameSmallItems

    PROVEN: the HTTP response body is itself a JSON-encoded string
    (double-encoded) - `response.json()` on the outer body yields a
    `str` that must be `json.loads`-ed again to reach the actual
    `{"EN": {...}}` object. `Sports`/`Regions`/`Champs`/
    `GameSmallItems` are each a dict keyed by the entry's own `ID` as a
    string, not a list - this **corrects** the earlier HYPOTHESIS that
    the top-level shape was `{"Sports": [...]}`.

    PROVEN: a `GameSmallItem`'s own `Sport`/`Region`/`Champ` fields are
    the parent node's numeric `ID` (foreign keys), not names - this
    **corrects** the earlier "item's own value wins" assumption. The
    parent `Sport`/`Region`/`Champ` node's `Name` is therefore always
    preferred; the item's own raw field is only used as a fallback
    when the parent node has no `Name` (e.g. a hypothetical variant
    payload, or the pre-existing unit-test fixtures that model
    `GameSmallItem.Sport` etc. as an already-resolved name string).
    `t1`/`t2` remain raw team ids - `getheader/en` carries no team
    lookup list to resolve them against (unlike `live/headernew/en`'s
    `Teams` list).
    """
    if isinstance(payload, str):
        payload = json.loads(payload)

    sports = _find_sports(payload)

    if sports is None:
        raise ValueError(
            "getheader/en response has no locatable "
            "'Sports' list - cannot distinguish a malformed/"
            "unexpected response from a legitimately empty one"
        )

    fixtures: list[Fixture] = []

    for sport_node in _iter_dicts(sports):
        sport_id = sport_node.get("ID")
        sport_name = sport_node.get("Name")

        for region_node in _iter_dicts(sport_node.get("Regions")):
            region_id = region_node.get("ID")
            region_name = region_node.get("Name")

            for champ_node in _iter_dicts(region_node.get("Champs")):
                champ_id = champ_node.get("ID")
                champ_name = champ_node.get("Name")

                for item in _iter_dicts(champ_node.get("GameSmallItems")):
                    fixture = parse_fixture_from_getheader_item(item)

                    fixtures.append(
                        replace(
                            fixture,
                            sport=(
                                sport_name if sport_name is not None else fixture.sport
                            ),
                            region=(
                                region_name
                                if region_name is not None
                                else fixture.region
                            ),
                            champ=(
                                champ_name if champ_name is not None else fixture.champ
                            ),
                            sport_id=sport_id,
                            region_id=region_id,
                            champ_id=champ_id,
                        )
                    )

    return tuple(fixtures)


def _find_sports(
    payload: Any,
) -> Any:
    """
    Locate the `Sports` collection within the decoded `getheader/en`
    response. PROVEN shape: a top-level `{"EN": {"Sports": {...}}}`
    wrapper, with `Sports` a dict keyed by id rather than a list; a
    bare top-level `{"Sports": [...]}` (the earlier HYPOTHESIS) is
    also tolerated for resilience/pre-existing test payloads.
    """
    if not isinstance(payload, dict):
        return None

    sports = payload.get("Sports")

    if isinstance(sports, (list, dict)):
        return sports

    for value in payload.values():
        if isinstance(value, dict) and isinstance(value.get("Sports"), (list, dict)):
            return value["Sports"]

    return None


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
