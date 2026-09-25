import json
from pathlib import Path

import pytest

from mystake.pipeline.live_header_parser import parse_live_header

LIVE_HEADER_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "mystake-live-header-sanitized.json"
)


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


def test_unrecognized_top_level_shape_raises_not_empties():
    # A structurally different/unrecognized root shape (no `Games`
    # key at all) must raise, never be mistaken for a legitimately
    # empty snapshot that would wipe the registry.
    with pytest.raises(ValueError):
        parse_live_header({"SomeOtherRootKey": []})


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


def _load_captured_live_header() -> dict:
    with LIVE_HEADER_FIXTURE_PATH.open("rb") as handle:
        return json.load(handle)


def test_captured_fixture_parses_all_84_games():
    payload = _load_captured_live_header()

    fixtures = parse_live_header(payload)

    assert len(fixtures) == 84
    assert len({fixture.game_id for fixture in fixtures}) == 84


def test_captured_fixture_resolves_lookup_names_by_id():
    payload = _load_captured_live_header()

    fixtures = parse_live_header(payload)

    sample = next(f for f in fixtures if f.game_id == 76509222)

    assert sample.sport_id == 1
    assert sample.sport == "Soccer"
    assert sample.region_id == 48
    assert sample.region == "Chile"
    assert sample.champ_id == 106505
    assert sample.champ == "Copa Chile, Knockout stage"
    assert sample.team1_id == 10963
    assert sample.team1 == "CD Everton Vina del Mar"
    assert sample.team2_id == 10975
    assert sample.team2 == "Universidad de Chile"
    assert sample.start_time == "2026-09-24T23:30:00"
    assert sample.source == "live_headernew"


def test_captured_fixture_preserves_raw_undocumented_fields():
    payload = _load_captured_live_header()

    fixtures = parse_live_header(payload)

    sample = next(f for f in fixtures if f.game_id == 76509222)

    # `MatchStatusID`, `ls`, `bgid`, etc. are undocumented/UNKNOWN -
    # they must survive untouched on `raw`, never dropped.
    assert sample.raw["MatchStatusID"] == 3
    assert sample.raw["ls"] == 3
    assert sample.raw["bgid"] == 74321594


def test_captured_fixture_every_game_resolves_through_all_five_lookups():
    payload = _load_captured_live_header()

    fixtures = parse_live_header(payload)

    sport_ids = {s["ID"] for s in payload["Sports"]}
    region_ids = {r["ID"] for r in payload["Regions"]}
    champ_ids = {c["ID"] for c in payload["Championats"]}
    team_ids = {t["ID"] for t in payload["Teams"]}

    for fixture in fixtures:
        assert fixture.sport_id in sport_ids
        assert fixture.region_id in region_ids
        assert fixture.champ_id in champ_ids
        assert fixture.team1_id in team_ids
        assert fixture.team2_id in team_ids
        # Every id in the capture resolves, so the resolved name is
        # never left equal to the raw id (would indicate a missed
        # lookup for this particular capture).
        assert fixture.sport != fixture.sport_id
        assert fixture.region != fixture.region_id
        assert fixture.champ != fixture.champ_id
        assert fixture.team1 != fixture.team1_id
        assert fixture.team2 != fixture.team2_id


def test_missing_lookup_entry_falls_back_to_id_without_dropping_fixture():
    payload = {
        "Games": [
            {
                "ID": 1,
                "Sport": 999,
                "Region": 999,
                "Champ": 999,
                "Team1": 999,
                "Team2": 998,
            }
        ],
        "Sports": [{"ID": 1, "Name": "Soccer"}],
        "Regions": [],
        "Championats": [],
        "Teams": [{"ID": 998, "Name": "Known Team"}],
    }

    fixtures = parse_live_header(payload)

    assert len(fixtures) == 1

    fixture = fixtures[0]

    assert fixture.game_id == 1
    # No matching Sports/Regions/Championats entry -> id retained
    # honestly as the "name", not silently dropped or guessed.
    assert fixture.sport == 999
    assert fixture.region == 999
    assert fixture.champ == 999
    assert fixture.team1 == 999
    # Team2 does resolve -> name is used.
    assert fixture.team2 == "Known Team"
