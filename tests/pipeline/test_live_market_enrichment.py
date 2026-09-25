from mystake.pipeline.live_market_enrichment import build_selection_metadata_lookup


def _real_snapshot_fragment() -> dict:
    """
    Trimmed fragment of a real `live/gamenew/{GameId}` payload
    (GameId 75832139, Soccer - Phase 4A observation, see
    docs/product/SCHEMA.md section 3).
    """
    return {
        "Match": {"GameID": 75832139},
        "gmk": [
            {
                "id": 2876337561,
                "mid": 616,
                "pid": 2206,
                "v": 2.2,
                "pn": "under",
                "posn": 2,
                "h": 2.5,
                "visible": True,
            },
            {
                "id": 2876337562,
                "mid": 616,
                "pid": 2207,
                "v": 1.6,
                "pn": "over",
                "posn": 1,
                "h": 2.5,
                "visible": True,
            },
            {
                "id": 2876337662,
                "mid": 602,
                "pid": 2104,
                "v": 1.01,
                "pn": "1",
                "posn": 1,
                "visible": True,
            },
        ],
        "mk": [
            {
                "ID": 616,
                "Sport": 1,
                "Name": "Total hometeam",
                "IsHandicap": False,
                "IsOverUnder": True,
                "ColumnCount": 2,
            },
            {
                "ID": 602,
                "Sport": 1,
                "Name": "3way",
                "IsHandicap": False,
                "IsOverUnder": False,
                "ColumnCount": 3,
            },
        ],
    }


def test_builds_lookup_with_market_and_selection_names_and_line():
    lookup = build_selection_metadata_lookup(_real_snapshot_fragment())

    metadata = lookup[2876337561]

    assert metadata.market_id == 616
    assert metadata.market_name == "Total hometeam"
    assert metadata.selection_name == "under"
    assert metadata.line == 2.5


def test_selection_without_h_gets_none_line():
    lookup = build_selection_metadata_lookup(_real_snapshot_fragment())

    metadata = lookup[2876337662]

    assert metadata.market_name == "3way"
    assert metadata.selection_name == "1"
    assert metadata.line is None


def test_missing_gmk_returns_empty_lookup():
    assert build_selection_metadata_lookup({}) == {}


def test_lookup_per_game_snapshot_does_not_leak_across_games():
    """
    Two independently-built lookups from two different games' own
    snapshots must not share entries, mirroring the per-GameId
    isolation the rest of the live pipeline guarantees.
    """
    snapshot_a = _real_snapshot_fragment()
    snapshot_b = {
        "Match": {"GameID": 999},
        "gmk": [
            {"id": 1, "mid": 1, "v": 1.5, "pn": "yes", "visible": True},
        ],
        "mk": [{"ID": 1, "Name": "Goal/No goal"}],
    }

    lookup_a = build_selection_metadata_lookup(snapshot_a)
    lookup_b = build_selection_metadata_lookup(snapshot_b)

    assert 1 not in lookup_a
    assert 2876337561 not in lookup_b
    assert lookup_b[1].market_name == "Goal/No goal"


def test_missing_metadata_for_unknown_selection_id_is_absent_not_fabricated():
    lookup = build_selection_metadata_lookup(_real_snapshot_fragment())

    assert lookup.get(999999999) is None
