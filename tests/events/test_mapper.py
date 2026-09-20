from mystake.events.mapper import (
    map_live_diff_to_events,
)
from mystake.events.models import (
    LiveEventType,
)
from mystake.pipeline.live_snapshot_diff import (
    diff_live_snapshots,
)


def test_maps_clock_change():
    previous = {
        "Match": {
            "GameID": 100,
            "MatchTimeExtended": "50:10",
        },
        "gmk": [],
        "TimeLines": [],
    }

    current = {
        "Match": {
            "GameID": 100,
            "MatchTimeExtended": "50:40",
        },
        "gmk": [],
        "TimeLines": [],
    }

    diff = diff_live_snapshots(
        previous,
        current,
    )

    events = map_live_diff_to_events(
        diff,
        current,
    )

    assert len(events) == 1

    event = events[0]

    assert (
        event.event_type
        == LiveEventType.CLOCK_CHANGED
    )
    assert event.game_id == 100
    assert event.payload["old"] == "50:10"
    assert event.payload["new"] == "50:40"


def test_maps_score_change():
    previous = {
        "Match": {
            "GameID": 100,
            "Score": "1:0",
        },
        "gmk": [],
        "TimeLines": [],
    }

    current = {
        "Match": {
            "GameID": 100,
            "Score": "2:0",
        },
        "gmk": [],
        "TimeLines": [],
    }

    diff = diff_live_snapshots(
        previous,
        current,
    )

    events = map_live_diff_to_events(
        diff,
        current,
    )

    assert len(events) == 1

    event = events[0]

    assert (
        event.event_type
        == LiveEventType.SCORE_CHANGED
    )
    assert event.payload["old"] == "1:0"
    assert event.payload["new"] == "2:0"


def test_maps_corner_change():
    previous = {
        "Match": {
            "GameID": 100,
            "CornersTeam2": 4,
        },
        "gmk": [],
        "TimeLines": [],
    }

    current = {
        "Match": {
            "GameID": 100,
            "CornersTeam2": 5,
        },
        "gmk": [],
        "TimeLines": [],
    }

    diff = diff_live_snapshots(
        previous,
        current,
    )

    events = map_live_diff_to_events(
        diff,
        current,
    )

    assert len(events) == 1

    event = events[0]

    assert (
        event.event_type
        == LiveEventType.CORNER_CHANGED
    )
    assert event.payload["team"] == "team2"
    assert event.payload["old"] == 4
    assert event.payload["new"] == 5


def test_maps_bet_status_change():
    previous = {
        "Match": {
            "GameID": 100,
            "BetStatus": 1,
        },
        "gmk": [],
        "TimeLines": [],
    }

    current = {
        "Match": {
            "GameID": 100,
            "BetStatus": 0,
        },
        "gmk": [],
        "TimeLines": [],
    }

    diff = diff_live_snapshots(
        previous,
        current,
    )

    events = map_live_diff_to_events(
        diff,
        current,
    )

    assert len(events) == 1

    event = events[0]

    assert (
        event.event_type
        == LiveEventType.BET_STATUS_CHANGED
    )
    assert event.payload["old"] == 1
    assert event.payload["new"] == 0


def test_maps_price_change():
    previous = {
        "Match": {
            "GameID": 100,
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

    current = {
        "Match": {
            "GameID": 100,
        },
        "gmk": [
            {
                "id": 2001,
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

    events = map_live_diff_to_events(
        diff,
        current,
    )

    assert len(events) == 1

    event = events[0]

    assert (
        event.event_type
        == LiveEventType.PRICE_CHANGED
    )
    assert event.payload["selection_id"] == 2001
    assert event.payload["market_id"] == 55
    assert event.payload["old"] == 1.80
    assert event.payload["new"] == 1.72


def test_maps_selection_hidden():
    previous = {
        "Match": {
            "GameID": 100,
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

    current = {
        "Match": {
            "GameID": 100,
        },
        "gmk": [
            {
                "id": 2001,
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

    events = map_live_diff_to_events(
        diff,
        current,
    )

    assert len(events) == 1

    assert (
        events[0].event_type
        == LiveEventType.SELECTION_HIDDEN
    )


def test_maps_selection_visible():
    previous = {
        "Match": {
            "GameID": 100,
        },
        "gmk": [
            {
                "id": 2001,
                "mid": 55,
                "v": 1.80,
                "visible": False,
            }
        ],
        "TimeLines": [],
    }

    current = {
        "Match": {
            "GameID": 100,
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

    diff = diff_live_snapshots(
        previous,
        current,
    )

    events = map_live_diff_to_events(
        diff,
        current,
    )

    assert len(events) == 1

    assert (
        events[0].event_type
        == LiveEventType.SELECTION_VISIBLE
    )


def test_maps_new_timeline_item():
    previous = {
        "Match": {
            "GameID": 100,
        },
        "gmk": [],
        "TimeLines": [],
    }

    timeline_item = {
        "type": "goal",
        "team": "home",
        "clk": "61:20",
    }

    current = {
        "Match": {
            "GameID": 100,
        },
        "gmk": [],
        "TimeLines": [
            timeline_item
        ],
    }

    diff = diff_live_snapshots(
        previous,
        current,
    )

    events = map_live_diff_to_events(
        diff,
        current,
    )

    assert len(events) == 1

    event = events[0]

    assert (
        event.event_type
        == LiveEventType.TIMELINE_EVENT_ADDED
    )

    assert (
        event.payload["item"]
        == timeline_item
    )


def test_maps_multiple_changes():
    previous = {
        "Match": {
            "GameID": 100,
            "MatchTimeExtended": "76:10",
            "CornersTeam2": 4,
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

    current = {
        "Match": {
            "GameID": 100,
            "MatchTimeExtended": "76:40",
            "CornersTeam2": 5,
        },
        "gmk": [
            {
                "id": 2001,
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

    events = map_live_diff_to_events(
        diff,
        current,
    )

    event_types = {
        event.event_type
        for event in events
    }

    assert event_types == {
        LiveEventType.CLOCK_CHANGED,
        LiveEventType.CORNER_CHANGED,
        LiveEventType.PRICE_CHANGED,
    }


def test_no_diff_produces_no_events():
    snapshot = {
        "Match": {
            "GameID": 100,
            "Score": "1:0",
        },
        "gmk": [],
        "TimeLines": [],
    }

    diff = diff_live_snapshots(
        snapshot,
        snapshot,
    )

    events = map_live_diff_to_events(
        diff,
        snapshot,
    )

    assert events == ()

def test_maps_match_ended():
    previous = {
        "Match": {
            "GameID": 100,
            "Status": 1,
            "BetStatus": 1,
            "EventStatus": 4,
            "LiveBetStatus": True,
        },
        "gmk": [],
        "TimeLines": [],
    }

    current = {
        "Match": {
            "GameID": 100,
            "Status": 3,
            "BetStatus": 0,
            "EventStatus": 40,
            "LiveBetStatus": False,
        },
        "gmk": [],
        "TimeLines": [],
    }

    diff = diff_live_snapshots(
        previous,
        current,
    )

    events = map_live_diff_to_events(
        diff,
        current,
    )

    event_types = {
        event.event_type
        for event in events
    }

    assert LiveEventType.MATCH_ENDED in event_types
    assert LiveEventType.BET_STATUS_CHANGED in event_types

    match_ended_event = next(
        event
        for event in events
        if event.event_type
        == LiveEventType.MATCH_ENDED
    )

    assert match_ended_event.game_id == 100

    assert match_ended_event.payload["status"] == {
        "old": 1,
        "new": 3,
    }

    assert match_ended_event.payload["bet_status"] == {
        "old": 1,
        "new": 0,
    }

    assert match_ended_event.payload["event_status"] == {
        "old": 4,
        "new": 40,
    }

    assert match_ended_event.payload[
        "live_bet_status"
    ] == {
        "old": True,
        "new": False,
    }


def test_live_bet_status_false_alone_does_not_end_match():
    previous = {
        "Match": {
            "GameID": 100,
            "Status": 1,
            "BetStatus": 1,
            "EventStatus": 4,
            "LiveBetStatus": True,
        },
        "gmk": [],
        "TimeLines": [],
    }

    current = {
        "Match": {
            "GameID": 100,
            "Status": 1,
            "BetStatus": 1,
            "EventStatus": 4,
            "LiveBetStatus": False,
        },
        "gmk": [],
        "TimeLines": [],
    }

    diff = diff_live_snapshots(
        previous,
        current,
    )

    events = map_live_diff_to_events(
        diff,
        current,
    )

    event_types = {
        event.event_type
        for event in events
    }

    assert (
        LiveEventType.MATCH_ENDED
        not in event_types
    )

    assert (
        LiveEventType.BET_STATUS_CHANGED
        in event_types
    )