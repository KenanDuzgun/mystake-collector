from mystake.models.snapshot import (
    parse_live_snapshot,
    parse_prematch_snapshot,
)


def test_parses_prematch_snapshot():
    game = {
        "id": 76432070,
        "t1": 1,
        "t2": 2,
        "pc": 2,
        "ev": {
            "55": {
                "2001": {"coef": 1.80},
                "2002": {"coef": 2.10},
            },
        },
    }

    snapshot = parse_prematch_snapshot(game)

    assert snapshot.game_id == 76432070
    assert snapshot.source == "prematch_gamefull"
    assert len(snapshot.markets) == 1
    assert snapshot.raw is game


def test_prematch_snapshot_without_ev():
    snapshot = parse_prematch_snapshot({"id": 1})

    assert snapshot.markets == ()


def test_parses_live_snapshot():
    data = {
        "Match": {
            "GameID": 100,
            "Score": "1:0",
        },
        "gmk": [
            {
                "id": 2001,
                "mid": 55,
                "v": 1.80,
                "visible": True,
            }
        ],
        "TimeLines": [],
    }

    snapshot = parse_live_snapshot(data)

    assert snapshot.game_id == 100
    assert snapshot.source == "live"
    assert len(snapshot.markets) == 1
    assert snapshot.raw is data


def test_live_snapshot_missing_match_and_gmk():
    snapshot = parse_live_snapshot({})

    assert snapshot.game_id is None
    assert snapshot.markets == ()


def test_live_snapshot_joins_mk_into_market_name():
    data = {
        "Match": {"GameID": 100, "Score": "1:0"},
        "gmk": [
            {"id": 2001, "mid": 55, "v": 1.80, "pn": "under", "h": 2.5},
        ],
        "mk": [{"ID": 55, "Name": "Total", "IsHandicap": False}],
        "TimeLines": [],
    }

    snapshot = parse_live_snapshot(data)

    assert snapshot.markets[0].name == "Total"
    assert snapshot.markets[0].selections[0].name == "under"
    assert snapshot.markets[0].selections[0].line == 2.5
