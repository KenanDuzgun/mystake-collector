import pytest

from mystake.pipeline.live_header_parser import parse_live_header


def test_parses_games_list():
    payload = {
        "Games": [
            {"ID": 1, "Sport": "Soccer"},
            {"ID": 2, "Sport": "Basketball"},
        ],
        "Sports": [],
        "Regions": [],
        "Championats": [],
        "Teams": [],
        "mk": [],
    }

    fixtures = parse_live_header(payload)

    assert len(fixtures) == 2
    assert {fixture.game_id for fixture in fixtures} == {1, 2}
    assert all(fixture.source == "live_headernew" for fixture in fixtures)


def test_preserves_raw_game_entry():
    item = {"ID": 1, "SomeUnmodeledField": "x"}

    fixtures = parse_live_header({"Games": [item]})

    assert fixtures[0].raw is item


def test_empty_games_list_returns_no_fixtures():
    assert parse_live_header({"Games": []}) == ()


def test_missing_games_key_raises_instead_of_silently_emptying():
    """
    A payload with no `Games` key must raise rather than be treated
    as "zero fixtures" - the discovery service relies on this to
    preserve the previous registry instead of wiping it out.
    """
    with pytest.raises(ValueError):
        parse_live_header({"Sports": []})


def test_malformed_payload_raises():
    with pytest.raises(ValueError):
        parse_live_header(None)

    with pytest.raises(ValueError):
        parse_live_header([])

    with pytest.raises(ValueError):
        parse_live_header({})


def test_duplicate_game_ids_are_both_returned_by_parser():
    payload = {
        "Games": [
            {"ID": 1},
            {"ID": 1},
        ],
    }

    fixtures = parse_live_header(payload)

    assert len(fixtures) == 2
    assert all(fixture.game_id == 1 for fixture in fixtures)
