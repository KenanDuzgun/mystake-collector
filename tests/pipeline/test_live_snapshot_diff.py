from mystake.pipeline.live_snapshot_diff import (
    diff_live_snapshots,
)


def test_detects_match_changes():
    previous = {
        "Match": {
            "GameScore": "1:0",
            "MatchTime": "35:10",
            "CornersTeam1": 2,
            "CornersTeam2": 1,
        },
        "gmk": [],
        "TimeLines": [],
    }

    current = {
        "Match": {
            "GameScore": "2:0",
            "MatchTime": "36:01",
            "CornersTeam1": 3,
            "CornersTeam2": 1,
        },
        "gmk": [],
        "TimeLines": [],
    }

    diff = diff_live_snapshots(
        previous,
        current,
    )

    changes = {
        change.field: (
            change.old,
            change.new,
        )
        for change in diff.match_changes
    }

    assert changes["GameScore"] == (
        "1:0",
        "2:0",
    )
    assert changes["MatchTime"] == (
        "35:10",
        "36:01",
    )
    assert changes["CornersTeam1"] == (
        2,
        3,
    )


def test_detects_price_change():
    previous = {
        "Match": {},
        "gmk": [
            {
                "id": 1001,
                "mid": 55,
                "v": 1.80,
                "visible": True,
            }
        ],
        "TimeLines": [],
    }

    current = {
        "Match": {},
        "gmk": [
            {
                "id": 1001,
                "mid": 55,
                "v": 1.72,
                "visible": True,
            }
        ],
        "TimeLines": [],
    }

    diff = diff_live_snapshots(
        previous,
        current,
    )

    assert len(diff.price_changes) == 1

    change = diff.price_changes[0]

    assert change.selection_id == 1001
    assert change.market_id == 55
    assert change.old == 1.80
    assert change.new == 1.72


def test_detects_visibility_change():
    previous = {
        "Match": {},
        "gmk": [
            {
                "id": 1001,
                "mid": 55,
                "v": 1.80,
                "visible": True,
            }
        ],
        "TimeLines": [],
    }

    current = {
        "Match": {},
        "gmk": [
            {
                "id": 1001,
                "mid": 55,
                "v": 1.80,
                "visible": False,
            }
        ],
        "TimeLines": [],
    }

    diff = diff_live_snapshots(
        previous,
        current,
    )

    assert len(
        diff.visibility_changes
    ) == 1

    change = diff.visibility_changes[0]

    assert change.selection_id == 1001
    assert change.old is True
    assert change.new is False


def test_detects_added_and_removed_selection():
    previous = {
        "Match": {},
        "gmk": [
            {
                "id": 1001,
                "mid": 55,
                "v": 1.80,
            }
        ],
        "TimeLines": [],
    }

    current = {
        "Match": {},
        "gmk": [
            {
                "id": 2002,
                "mid": 60,
                "v": 2.10,
            }
        ],
        "TimeLines": [],
    }

    diff = diff_live_snapshots(
        previous,
        current,
    )

    assert len(
        diff.added_selections
    ) == 1
    assert (
        diff.added_selections[0].selection_id
        == 2002
    )

    assert len(
        diff.removed_selections
    ) == 1
    assert (
        diff.removed_selections[0].selection_id
        == 1001
    )


def test_detects_new_timeline_item():
    previous = {
        "Match": {},
        "gmk": [],
        "TimeLines": [
            {
                "type": "corner_kick",
                "team": "home",
                "clk": "5:30",
            }
        ],
    }

    current = {
        "Match": {},
        "gmk": [],
        "TimeLines": [
            {
                "type": "corner_kick",
                "team": "home",
                "clk": "5:30",
            },
            {
                "type": "goal",
                "team": "away",
                "clk": "10:14",
            },
        ],
    }

    diff = diff_live_snapshots(
        previous,
        current,
    )

    assert len(
        diff.new_timeline_items
    ) == 1

    assert (
        diff.new_timeline_items[0]["type"]
        == "goal"
    )


def test_no_changes():
    snapshot = {
        "Match": {
            "GameScore": "1:0",
            "MatchTime": "35:10",
        },
        "gmk": [
            {
                "id": 1001,
                "mid": 55,
                "v": 1.80,
                "visible": True,
            }
        ],
        "TimeLines": [],
    }

    diff = diff_live_snapshots(
        snapshot,
        snapshot,
    )

    assert diff.has_changes is False