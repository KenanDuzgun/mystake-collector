from mystake.models.fixture import (
    parse_fixture_from_getheader_item,
)


def test_parses_known_fields():
    item = {
        "GameId": 76432070,
        "Sport": "Soccer",
        "Region": "England",
        "Champ": "Premier League",
        "StartTime": 1790013300,
        "t1": "Team A",
        "t2": "Team B",
    }

    fixture = parse_fixture_from_getheader_item(
        item
    )

    assert fixture.game_id == 76432070
    assert fixture.sport == "Soccer"
    assert fixture.region == "England"
    assert fixture.champ == "Premier League"
    assert fixture.start_time == 1790013300
    assert fixture.team1 == "Team A"
    assert fixture.team2 == "Team B"


def test_preserves_unknown_fields_via_raw():
    item = {
        "GameId": 1,
        "Sport": "Tennis",
        "SomeUnmodeledField": "unexpected-value",
        "AnotherField": {"nested": True},
    }

    fixture = parse_fixture_from_getheader_item(
        item
    )

    assert fixture.raw == item
    assert fixture.raw["SomeUnmodeledField"] == (
        "unexpected-value"
    )
    assert fixture.raw is item


def test_missing_fields_become_none():
    fixture = parse_fixture_from_getheader_item(
        {}
    )

    assert fixture.game_id is None
    assert fixture.sport is None
    assert fixture.raw == {}
