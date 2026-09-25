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
    assert selection.name is None
    assert selection.line is None
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
    assert selection.name is None
    assert selection.line is None


def test_live_selection_extracts_name_and_line_when_present():
    """
    Real `gmk` entry from Phase 4A observation (GameId 75832139,
    Soccer, market 616 "Total hometeam") - see docs/product/SCHEMA.md
    section 3.
    """
    data = {
        "id": 2876337561,
        "mid": 616,
        "pid": 2206,
        "v": 2.2,
        "pn": "under",
        "posn": 2,
        "h": 2.5,
        "visible": True,
    }

    selection = parse_live_selection(data)

    assert selection.name == "under"
    assert selection.line == 2.5


def test_live_selection_missing_pn_and_h_become_none():
    """
    Real `gmk` entries for 3-way/odd-even markets carry no `h`
    (e.g. `{"id": 2876337662, "mid": 602, "pid": 2104, "v": 1.01,
    "pn": "1", "posn": 1, "visible": true}`), so `line` must be `None`
    rather than a fabricated value; a selection with no `pn` at all
    must likewise get `name=None`.
    """
    selection = parse_live_selection({"id": 1, "mid": 2, "v": 1.5, "visible": True})

    assert selection.name is None
    assert selection.line is None
