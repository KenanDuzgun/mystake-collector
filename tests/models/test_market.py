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

    markets_by_id = {
        market.id: market for market in markets
    }

    market_55 = markets_by_id["55"]

    assert len(market_55.selections) == 2
    assert market_55.raw is ev["55"]

    selection_ids = {
        selection.id
        for selection in market_55.selections
    }

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

    markets_by_id = {
        market.id: market for market in markets
    }

    assert len(markets_by_id[55].selections) == 2
    assert len(markets_by_id[60].selections) == 1


def test_live_markets_empty_input():
    assert parse_live_markets([]) == ()
    assert parse_live_markets(None) == ()
