from mystake.models.market import (
    parse_live_markets,
    parse_prematch_markets,
)


def test_parses_prematch_markets():
    ev = {
        "55": {
            "2001": {"coef": 1.80, "lock": False},
            "2002": {"coef": 2.10, "lock": False},
        },
        "60": {
            "3001": {"coef": 1.50, "lock": True},
        },
    }

    markets = parse_prematch_markets(ev)

    assert len(markets) == 2

    markets_by_id = {market.id: market for market in markets}

    market_55 = markets_by_id["55"]

    assert len(market_55.selections) == 2
    assert market_55.raw is ev["55"]

    selection_ids = {selection.id for selection in market_55.selections}

    assert selection_ids == {"2001", "2002"}


def test_prematch_markets_skip_non_dict_entries():
    ev = {
        "55": {"2001": {"coef": 1.80}},
        "junk": "not-a-market",
    }

    markets = parse_prematch_markets(ev)

    assert len(markets) == 1
    assert markets[0].id == "55"


def test_parses_live_markets_grouped_by_mid():
    gmk = [
        {"id": 2001, "mid": 55, "v": 1.80, "visible": True},
        {"id": 2002, "mid": 55, "v": 2.10, "visible": True},
        {"id": 3001, "mid": 60, "v": 1.50, "visible": True},
    ]

    markets = parse_live_markets(gmk)

    assert len(markets) == 2

    markets_by_id = {market.id: market for market in markets}

    assert len(markets_by_id[55].selections) == 2
    assert len(markets_by_id[60].selections) == 1


def test_live_markets_empty_input():
    assert parse_live_markets([]) == ()
    assert parse_live_markets(None) == ()


def test_live_markets_without_mk_have_none_metadata():
    gmk = [{"id": 2001, "mid": 55, "v": 1.80, "visible": True}]

    markets = parse_live_markets(gmk)

    assert markets[0].name is None
    assert markets[0].is_handicap is None
    assert markets[0].column_count is None


def test_live_markets_joined_with_mk_by_id_eq_mid():
    """
    Real Phase 4A observation (GameId 75832139): `mk[].ID` joins
    1:1 with `gmk[].mid` - see docs/product/SCHEMA.md section 3.
    """
    gmk = [
        {"id": 2876337561, "mid": 616, "v": 2.2, "pn": "under", "h": 2.5},
        {"id": 2876338016, "mid": 604, "v": 18.0, "pn": "1", "h": -4.0},
    ]
    mk = [
        {
            "ID": 616,
            "Name": "Total hometeam",
            "IsHandicap": False,
            "IsOverUnder": True,
            "ColumnCount": 2,
        },
        {
            "ID": 604,
            "Name": "Handicap",
            "IsHandicap": True,
            "ColumnCount": 3,
        },
    ]

    markets = parse_live_markets(gmk, mk)
    markets_by_id = {market.id: market for market in markets}

    assert markets_by_id[616].name == "Total hometeam"
    assert markets_by_id[616].is_handicap is False
    assert markets_by_id[616].column_count == 2

    assert markets_by_id[604].name == "Handicap"
    assert markets_by_id[604].is_handicap is True
    assert markets_by_id[604].column_count == 3


def test_live_markets_unmatched_mid_gets_none_metadata():
    gmk = [{"id": 1, "mid": 999, "v": 1.5}]
    mk = [{"ID": 616, "Name": "Total hometeam"}]

    markets = parse_live_markets(gmk, mk)

    assert markets[0].name is None
    assert markets[0].is_handicap is None
    assert markets[0].column_count is None


def test_prematch_markets_have_none_metadata():
    ev = {"55": {"2001": {"coef": 1.80}}}

    markets = parse_prematch_markets(ev)

    assert markets[0].name is None
    assert markets[0].is_handicap is None
    assert markets[0].column_count is None
