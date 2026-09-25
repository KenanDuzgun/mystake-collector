from mystake.models.selection import (
    parse_live_selection,
    parse_prematch_selection,
)


def test_parses_prematch_selection():
    data = {
        "coef": 1.85,
        "lock": False,
        "someUnmodeledField": "x",
    }

    selection = parse_prematch_selection(
        "2001",
        data,
        market_id="55",
    )

    assert selection.id == "2001"
    assert selection.market_id == "55"
    assert selection.price == 1.85
    assert selection.locked is False
    assert selection.visible is None
    assert selection.raw is data


def test_prematch_selection_coerces_string_price():
    data = {"coef": "1.90"}

    selection = parse_prematch_selection(
        1,
        data,
        market_id=None,
    )

    assert selection.price == 1.90


def test_parses_live_selection():
    data = {
        "id": 2001,
        "mid": 55,
        "v": 1.72,
        "visible": True,
    }

    selection = parse_live_selection(data)

    assert selection.id == 2001
    assert selection.market_id == 55
    assert selection.price == 1.72
    assert selection.visible is True
    assert selection.locked is None
    assert selection.raw is data


def test_live_selection_missing_fields_become_none():
    selection = parse_live_selection({})

    assert selection.id is None
    assert selection.market_id is None
    assert selection.price is None
    assert selection.visible is None
