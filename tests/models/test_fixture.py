from mystake.models.fixture import (
    parse_fixture_from_getheader_item,
    parse_fixture_from_live_game_item,
)


def test_parses_known_fields():
    item = {
        "ID": 76432070,
        "Sport": "Soccer",
        "Region": "England",
        "Champ": "Premier League",
        "StartTime": 1790013300,
        "t1": "Team A",
        "t2": "Team B",
    }

    fixture = parse_fixture_from_getheader_item(item)

    assert fixture.game_id == 76432070
    assert fixture.sport == "Soccer"
    assert fixture.region == "England"
    assert fixture.champ == "Premier League"
    assert fixture.start_time == 1790013300
    assert fixture.team1 == "Team A"
    assert fixture.team2 == "Team B"
    assert fixture.source == "prematch_getheader"


def test_falls_back_to_game_id_field():
    """
    `GameId` is not the observed `GameSmallItem` identifier field
    (`ID` is - see docs/product/SCHEMA.md), but is kept as a
    resilience fallback in case a variant payload uses it.
    """
    item = {
        "GameId": 76432070,
        "Sport": "Soccer",
    }

    fixture = parse_fixture_from_getheader_item(item)

    assert fixture.game_id == 76432070


def test_id_field_takes_priority_over_game_id_fallback():
    item = {
        "ID": 1,
        "GameId": 2,
    }

    fixture = parse_fixture_from_getheader_item(item)

    assert fixture.game_id == 1


def test_preserves_unknown_fields_via_raw():
    item = {
        "ID": 1,
        "Sport": "Tennis",
        "SomeUnmodeledField": "unexpected-value",
        "AnotherField": {"nested": True},
    }

    fixture = parse_fixture_from_getheader_item(item)

    assert fixture.raw == item
    assert fixture.raw["SomeUnmodeledField"] == ("unexpected-value")
    assert fixture.raw is item


def test_missing_fields_become_none():
    fixture = parse_fixture_from_getheader_item({})

    assert fixture.game_id is None
    assert fixture.sport is None
    assert fixture.raw == {}


def test_parse_live_game_item_sets_source():
    item = {
        "ID": 76432070,
        "Sport": "Soccer",
    }

    fixture = parse_fixture_from_live_game_item(item)

    assert fixture.game_id == 76432070
    assert fixture.source == "live_headernew"
    assert fixture.raw is item


def test_parse_live_game_item_missing_fields_become_none():
    fixture = parse_fixture_from_live_game_item({})

    assert fixture.game_id is None
    assert fixture.sport is None
    assert fixture.source == "live_headernew"
