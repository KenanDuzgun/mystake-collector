from __future__ import annotations

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

    Observed hierarchy (PROVEN this session):

        Sports -> Regions -> Champs -> GameSmallItems

    `Sport`/`Region`/`Champ` node `ID`/`Name` fields are HYPOTHESIS,
    inferred from the `GameSmallItem.ID` convention (see
    docs/product/SCHEMA.md) - not yet independently verified. If a
    `Sport`/`Region`/`Champ` node's own name differs from the name
    embedded on the `GameSmallItem` itself, the item's own value wins
    (it is the STRONG EVIDENCE field); the parent node's name is only
    used as a fallback when the item omits it.
    """
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
                                fixture.sport
                                if fixture.sport is not None
                                else sport_name
                            ),
                            region=(
                                fixture.region
                                if fixture.region is not None
                                else region_name
                            ),
                            champ=(
                                fixture.champ
                                if fixture.champ is not None
                                else champ_name
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
    Locate the `Sports` list within the decoded `getheader/en`
    response. The response is expected to be a dict with a top-level
    `Sports` key; this also tolerates one extra level of nesting (e.g.
    a language-keyed wrapper) since the exact outer envelope has not
    been independently verified.
    """
    if not isinstance(payload, dict):
        return None

    sports = payload.get("Sports")

    if isinstance(sports, list):
        return sports

    for value in payload.values():
        if isinstance(value, dict) and isinstance(value.get("Sports"), list):
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
