import datetime

from mystake.models.fixture import Fixture
from observe_prematch_to_live import (
    match_prematch_to_live,
    parse_start_time,
    select_candidate_fixtures,
)


def make_prematch_fixture(
    game_id,
    *,
    sport="Soccer",
    start_time="2026-09-25T12:20:00",
    t1=111,
    t2=222,
    champ="Champions League",
) -> Fixture:
    return Fixture(
        game_id=game_id,
        sport=sport,
        region="Region",
        champ=champ,
        start_time=start_time,
        team1=t1,
        team2=t2,
        raw={"ID": game_id, "t1": t1, "t2": t2, "StartTime": start_time},
        source="prematch_getheader",
    )


def make_live_fixture(
    game_id,
    *,
    sport="Soccer",
    team1_id=111,
    team2_id=222,
) -> Fixture:
    return Fixture(
        game_id=game_id,
        sport=sport,
        region="Region",
        champ="Champions League",
        start_time="2026-09-25T12:20:00",
        team1="Team A",
        team2="Team B",
        raw={"ID": game_id},
        source="live_headernew",
        team1_id=team1_id,
        team2_id=team2_id,
    )


def test_parse_start_time_valid():
    assert parse_start_time("2026-09-25T12:20:00") == datetime.datetime(  # noqa: DTZ001
        2026, 9, 25, 12, 20, 0
    )


def test_parse_start_time_invalid_returns_none():
    assert parse_start_time("not-a-date") is None
    assert parse_start_time(None) is None
    assert parse_start_time(12345) is None


def test_select_candidate_fixtures_filters_by_sport_window_and_game_id():
    now = datetime.datetime(2026, 9, 25, 12, 0, 0)  # noqa: DTZ001

    fixtures = (
        make_prematch_fixture(1, sport="Soccer", start_time="2026-09-25T12:05:00"),
        make_prematch_fixture(2, sport="Tennis", start_time="2026-09-25T12:05:00"),
        make_prematch_fixture(3, sport="Soccer", start_time="2026-09-25T12:30:00"),
        make_prematch_fixture(4, sport="Soccer", start_time="2026-09-25T11:00:00"),
        make_prematch_fixture(5, sport="Soccer", start_time="bad-value"),
    )

    selected = select_candidate_fixtures(
        fixtures,
        now=now,
        sport="Soccer",
        lookahead_minutes=15,
        max_candidates=3,
    )

    assert [f.game_id for f in selected] == [1]


def test_select_candidate_fixtures_sorts_soonest_first_and_caps_max_candidates():
    now = datetime.datetime(2026, 9, 25, 12, 0, 0)  # noqa: DTZ001

    fixtures = (
        make_prematch_fixture(1, start_time="2026-09-25T12:12:00"),
        make_prematch_fixture(2, start_time="2026-09-25T12:02:00"),
        make_prematch_fixture(3, start_time="2026-09-25T12:07:00"),
        make_prematch_fixture(4, start_time="2026-09-25T12:01:00"),
    )

    selected = select_candidate_fixtures(
        fixtures,
        now=now,
        sport="Soccer",
        lookahead_minutes=15,
        max_candidates=3,
    )

    assert [f.game_id for f in selected] == [4, 2, 3]


def test_select_candidate_fixtures_excludes_none_game_id():
    now = datetime.datetime(2026, 9, 25, 12, 0, 0)  # noqa: DTZ001

    fixtures = (make_prematch_fixture(None, start_time="2026-09-25T12:05:00"),)

    selected = select_candidate_fixtures(
        fixtures, now=now, sport="Soccer", lookahead_minutes=15, max_candidates=3
    )

    assert selected == []


def test_match_prematch_to_live_exact_game_id_wins_over_team_id_pair():
    candidate = make_prematch_fixture(100, t1=111, t2=222)
    live_fixtures = (
        make_live_fixture(100, team1_id=999, team2_id=888),
        make_live_fixture(200, team1_id=111, team2_id=222),
    )

    evidence = match_prematch_to_live(candidate, live_fixtures)

    assert evidence.kind == "exact_game_id"
    assert [f.game_id for f in evidence.live_fixtures] == [100]


def test_match_prematch_to_live_team_id_pair_is_order_independent():
    candidate = make_prematch_fixture(100, t1=111, t2=222)
    live_fixtures = (make_live_fixture(200, team1_id=222, team2_id=111),)

    evidence = match_prematch_to_live(candidate, live_fixtures)

    assert evidence.kind == "team_id_pair"
    assert [f.game_id for f in evidence.live_fixtures] == [200]


def test_match_prematch_to_live_ambiguous_when_multiple_team_id_pair_matches():
    candidate = make_prematch_fixture(100, t1=111, t2=222)
    live_fixtures = (
        make_live_fixture(200, team1_id=111, team2_id=222),
        make_live_fixture(300, team1_id=222, team2_id=111),
    )

    evidence = match_prematch_to_live(candidate, live_fixtures)

    assert evidence.kind == "ambiguous_team_id_pair"
    assert {f.game_id for f in evidence.live_fixtures} == {200, 300}


def test_match_prematch_to_live_none_when_no_overlap():
    candidate = make_prematch_fixture(100, t1=111, t2=222)
    live_fixtures = (make_live_fixture(200, team1_id=333, team2_id=444),)

    evidence = match_prematch_to_live(candidate, live_fixtures)

    assert evidence.kind == "none"
    assert evidence.live_fixtures == ()


def test_match_prematch_to_live_none_when_candidate_has_no_team_ids():
    candidate = make_prematch_fixture(100, t1=None, t2=None)
    live_fixtures = (make_live_fixture(200, team1_id=111, team2_id=222),)

    evidence = match_prematch_to_live(candidate, live_fixtures)

    assert evidence.kind == "none"
