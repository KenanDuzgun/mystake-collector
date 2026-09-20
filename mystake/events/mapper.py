from __future__ import annotations

from typing import Any

from mystake.events.models import (
    LiveDomainEvent,
    LiveEventType,
)
from mystake.pipeline.live_snapshot_diff import (
    LiveSnapshotDiff,
)


CLOCK_FIELDS = (
    "MatchTimeExtended",
    "MatchTime",
)

SCORE_FIELDS = (
    "Score",
    "GameScore",
)

BET_STATUS_FIELDS = (
    "BetStatus",
    "LiveBetStatus",
)

CORNER_FIELDS = {
    "CornersTeam1": "team1",
    "CornersTeam2": "team2",
}


def map_live_diff_to_events(
    diff: LiveSnapshotDiff,
    current_snapshot: dict[str, Any],
) -> tuple[LiveDomainEvent, ...]:
    events: list[LiveDomainEvent] = []

    game_id = _get_game_id(
        current_snapshot
    )

    match_changes = {
        change.field: change
        for change in diff.match_changes
    }

    clock_change = _first_change(
        match_changes,
        CLOCK_FIELDS,
    )

    if clock_change is not None:
        events.append(
            LiveDomainEvent(
                event_type=LiveEventType.CLOCK_CHANGED,
                game_id=game_id,
                payload={
                    "field": clock_change.field,
                    "old": clock_change.old,
                    "new": clock_change.new,
                },
            )
        )

    score_change = _first_change(
        match_changes,
        SCORE_FIELDS,
    )

    if score_change is not None:
        events.append(
            LiveDomainEvent(
                event_type=LiveEventType.SCORE_CHANGED,
                game_id=game_id,
                payload={
                    "field": score_change.field,
                    "old": score_change.old,
                    "new": score_change.new,
                },
            )
        )

    bet_status_change = _first_change(
        match_changes,
        BET_STATUS_FIELDS,
    )

    if bet_status_change is not None:
        events.append(
            LiveDomainEvent(
                event_type=(
                    LiveEventType.BET_STATUS_CHANGED
                ),
                game_id=game_id,
                payload={
                    "field": bet_status_change.field,
                    "old": bet_status_change.old,
                    "new": bet_status_change.new,
                },
            )
        )

    for field, team in CORNER_FIELDS.items():
        change = match_changes.get(field)

        if change is None:
            continue

        events.append(
            LiveDomainEvent(
                event_type=LiveEventType.CORNER_CHANGED,
                game_id=game_id,
                payload={
                    "team": team,
                    "field": field,
                    "old": change.old,
                    "new": change.new,
                },
            )
        )

    for change in diff.price_changes:
        events.append(
            LiveDomainEvent(
                event_type=LiveEventType.PRICE_CHANGED,
                game_id=game_id,
                payload={
                    "selection_id": (
                        change.selection_id
                    ),
                    "market_id": (
                        change.market_id
                    ),
                    "old": change.old,
                    "new": change.new,
                },
            )
        )

    for change in diff.visibility_changes:
        if change.new is True:
            event_type = (
                LiveEventType.SELECTION_VISIBLE
            )
        elif change.new is False:
            event_type = (
                LiveEventType.SELECTION_HIDDEN
            )
        else:
            continue

        events.append(
            LiveDomainEvent(
                event_type=event_type,
                game_id=game_id,
                payload={
                    "selection_id": (
                        change.selection_id
                    ),
                    "market_id": (
                        change.market_id
                    ),
                    "old": change.old,
                    "new": change.new,
                },
            )
        )

    for item in diff.new_timeline_items:
        events.append(
            LiveDomainEvent(
                event_type=(
                    LiveEventType.TIMELINE_EVENT_ADDED
                ),
                game_id=game_id,
                payload={
                    "item": item,
                },
            )
        )

    return tuple(events)


def _get_game_id(
    snapshot: dict[str, Any],
) -> int | str | None:
    match = snapshot.get("Match")

    if not isinstance(match, dict):
        return None

    return match.get("GameID")


def _first_change(
    changes: dict[str, Any],
    fields: tuple[str, ...],
):
    for field in fields:
        change = changes.get(field)

        if change is not None:
            return change

    return None