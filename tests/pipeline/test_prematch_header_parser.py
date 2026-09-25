import json

import pytest

from mystake.pipeline.prematch_header_parser import parse_prematch_header


def build_payload():
    return {
        "Sports": [
            {
                "ID": 1,
                "Name": "Soccer",
                "Regions": [
                    {
                        "ID": 10,
                        "Name": "England",
                        "Champs": [
                            {
                                "ID": 100,
                                "Name": "Premier League",
                                "GameSmallItems": [
                                    {
                                        "ID": 76432070,
                                        "Sport": "Soccer",
                                        "Region": "England",
                                        "Champ": "Premier League",
                                        "StartTime": 1790013300,
                                        "t1": "Team A",
                                        "t2": "Team B",
                                    },
                                ],
                            },
                        ],
                    },
                ],
            },
            {
                "ID": 2,
                "Name": "Basketball",
                "Regions": [
                    {
                        "ID": 20,
                        "Name": "USA",
                        "Champs": [
                            {
                                "ID": 200,
                                "Name": "NBA",
                                "GameSmallItems": [
                                    {
                                        "ID": 76432999,
                                        "Sport": "Basketball",
                                        "Region": "USA",
                                        "Champ": "NBA",
                                        "StartTime": 1790099999,
                                        "t1": "Team C",
                                        "t2": "Team D",
                                    },
                                ],
                            },
                        ],
                    },
                ],
            },
        ],
    }


def test_parses_fixtures_across_multiple_sports():
    fixtures = parse_prematch_header(build_payload())

    assert len(fixtures) == 2

    game_ids = {fixture.game_id for fixture in fixtures}
    assert game_ids == {76432070, 76432999}


def test_populates_hierarchy_ids_and_source():
    fixtures = parse_prematch_header(build_payload())

    soccer_fixture = next(
        fixture for fixture in fixtures if fixture.game_id == 76432070
    )

    assert soccer_fixture.sport == "Soccer"
    assert soccer_fixture.sport_id == 1
    assert soccer_fixture.region == "England"
    assert soccer_fixture.region_id == 10
    assert soccer_fixture.champ == "Premier League"
    assert soccer_fixture.champ_id == 100
    assert soccer_fixture.source == "prematch_getheader"


def test_falls_back_to_parent_name_when_item_omits_it():
    payload = {
        "Sports": [
            {
                "ID": 1,
                "Name": "Soccer",
                "Regions": [
                    {
                        "ID": 10,
                        "Name": "England",
                        "Champs": [
                            {
                                "ID": 100,
                                "Name": "Premier League",
                                "GameSmallItems": [
                                    {
                                        "ID": 1,
                                        "StartTime": 1,
                                    },
                                ],
                            },
                        ],
                    },
                ],
            },
        ],
    }

    fixtures = parse_prematch_header(payload)

    assert fixtures[0].sport == "Soccer"
    assert fixtures[0].region == "England"
    assert fixtures[0].champ == "Premier League"


def test_empty_payload_returns_no_fixtures():
    assert parse_prematch_header({"Sports": []}) == ()


def test_malformed_payload_raises_instead_of_silently_emptying():
    """
    A payload with no locatable `Sports` list must raise rather than
    be treated as "zero fixtures" - the caller (discovery service)
    relies on this to distinguish a malformed response from a
    legitimately empty one and preserve the previous registry state
    instead of wiping it.
    """
    with pytest.raises(ValueError):
        parse_prematch_header(None)

    with pytest.raises(ValueError):
        parse_prematch_header([])

    with pytest.raises(ValueError):
        parse_prematch_header({})


def test_parses_double_json_encoded_en_wrapped_dict_hierarchy():
    """
    PROVEN against a real `getheader/en` response this session: the
    HTTP body decodes (via `response.json()`) to a `str`, which is
    itself JSON containing `{"EN": {"Sports": {<id>: {...}}}}` -
    `Sports`/`Regions`/`Champs`/`GameSmallItems` are dicts keyed by id,
    not lists.
    """
    inner = {
        "EN": {
            "Sports": {
                "23": {
                    "ID": 23,
                    "Name": "Field Hockey",
                    "Regions": {
                        "152": {
                            "ID": 152,
                            "Name": "International",
                            "Champs": {
                                "20109": {
                                    "ID": 20109,
                                    "Name": "Asian Games",
                                    "GameSmallItems": {
                                        "76607029": {
                                            "ID": 76607029,
                                            "Champ": 20109,
                                            "Region": 152,
                                            "Sport": 23,
                                            "t1": 684046,
                                            "t2": 218694,
                                            "StartTime": "2026-09-25T13:00:00",
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    }
    payload = json.dumps(inner)

    fixtures = parse_prematch_header(payload)

    assert len(fixtures) == 1

    fixture = fixtures[0]
    assert fixture.game_id == 76607029
    # The parent node's Name is authoritative, not the item's own
    # `Sport`/`Region`/`Champ` foreign-key id.
    assert fixture.sport == "Field Hockey"
    assert fixture.sport_id == 23
    assert fixture.region == "International"
    assert fixture.region_id == 152
    assert fixture.champ == "Asian Games"
    assert fixture.champ_id == 20109
    # No team lookup list exists on this endpoint - team ids stay raw.
    assert fixture.team1 == 684046
    assert fixture.team2 == 218694


def test_parent_name_wins_over_items_own_foreign_key_id():
    payload = {
        "Sports": [
            {
                "ID": 1,
                "Name": "Soccer",
                "Regions": [
                    {
                        "ID": 10,
                        "Name": "England",
                        "Champs": [
                            {
                                "ID": 100,
                                "Name": "Premier League",
                                "GameSmallItems": [
                                    {
                                        "ID": 1,
                                        # Item's own Sport/Region/Champ
                                        # are foreign-key ids, matching
                                        # the parent node's own ID -
                                        # never names.
                                        "Sport": 1,
                                        "Region": 10,
                                        "Champ": 100,
                                    },
                                ],
                            },
                        ],
                    },
                ],
            },
        ],
    }

    fixtures = parse_prematch_header(payload)

    assert fixtures[0].sport == "Soccer"
    assert fixtures[0].region == "England"
    assert fixtures[0].champ == "Premier League"


def test_duplicate_game_ids_are_both_returned_by_parser():
    """
    The parser itself does not deduplicate - GameId uniqueness is a
    registry-level concern (see tests/registry/test_fixture_registry.py).
    """
    payload = {
        "Sports": [
            {
                "ID": 1,
                "Name": "Soccer",
                "Regions": [
                    {
                        "ID": 10,
                        "Name": "England",
                        "Champs": [
                            {
                                "ID": 100,
                                "Name": "Premier League",
                                "GameSmallItems": [
                                    {"ID": 1, "StartTime": 1},
                                    {"ID": 1, "StartTime": 2},
                                ],
                            },
                        ],
                    },
                ],
            },
        ],
    }

    fixtures = parse_prematch_header(payload)

    assert len(fixtures) == 2
    assert all(fixture.game_id == 1 for fixture in fixtures)
