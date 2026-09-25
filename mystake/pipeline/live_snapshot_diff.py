from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MATCH_ENDED_FIELDS = (
    "Status",
    "BetStatus",
    "EventStatus",
    "LiveBetStatus",
)


MATCH_FIELDS = (
    "GameScore",
    "Score",
    "MatchTime",
    "MatchTimeExtended",
    "Status",
    "BetStatus",
    "EventStatus",
    "LiveBetStatus",
    "ClockStopped",
    "CornersTeam1",
    "CornersTeam2",
    "RedCardsTeam1",
    "RedCardsTeam2",
    "YellowCardsTeam1",
    "YellowCardsTeam2",
    "YellowRedCardsTeam1",
    "YellowRedCardsTeam2",
)


@dataclass(frozen=True)
class FieldChange:
    field: str
    old: Any
    new: Any


@dataclass(frozen=True)
class SelectionPriceChange:
    selection_id: int | str
    market_id: int | str | None
    old: Any
    new: Any


@dataclass(frozen=True)
class SelectionVisibilityChange:
    selection_id: int | str
    market_id: int | str | None
    old: bool | None
    new: bool | None


@dataclass(frozen=True)
class SelectionChange:
    selection_id: int | str
    market_id: int | str | None


@dataclass(frozen=True)
class LiveSnapshotDiff:
    match_changes: tuple[FieldChange, ...]
    price_changes: tuple[SelectionPriceChange, ...]
    visibility_changes: tuple[SelectionVisibilityChange, ...]
    added_selections: tuple[SelectionChange, ...]
    removed_selections: tuple[SelectionChange, ...]
    new_timeline_items: tuple[dict[str, Any], ...]
    match_ended: bool
    match_ended_context: tuple[FieldChange, ...]

    @property
    def has_changes(self) -> bool:
        return any(
            (
                self.match_changes,
                self.price_changes,
                self.visibility_changes,
                self.added_selections,
                self.removed_selections,
                self.new_timeline_items,
                self.match_ended,
            )
        )


def diff_live_snapshots(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> LiveSnapshotDiff:
    previous_match = _get_match(previous)
    current_match = _get_match(current)

    match_changes = _diff_match(
        previous_match,
        current_match,
    )

    previous_selections = _index_selections(
        previous.get("gmk"),
    )
    current_selections = _index_selections(
        current.get("gmk"),
    )

    previous_ids = set(previous_selections)
    current_ids = set(current_selections)

    added_ids = current_ids - previous_ids
    removed_ids = previous_ids - current_ids
    common_ids = previous_ids & current_ids

    added_selections = tuple(
        SelectionChange(
            selection_id=selection_id,
            market_id=current_selections[
                selection_id
            ].get("mid"),
        )
        for selection_id in sorted(
            added_ids,
            key=str,
        )
    )

    removed_selections = tuple(
        SelectionChange(
            selection_id=selection_id,
            market_id=previous_selections[
                selection_id
            ].get("mid"),
        )
        for selection_id in sorted(
            removed_ids,
            key=str,
        )
    )

    price_changes: list[SelectionPriceChange] = []
    visibility_changes: list[
        SelectionVisibilityChange
    ] = []

    for selection_id in sorted(
        common_ids,
        key=str,
    ):
        old_selection = previous_selections[
            selection_id
        ]
        new_selection = current_selections[
            selection_id
        ]

        old_price = old_selection.get("v")
        new_price = new_selection.get("v")

        if old_price != new_price:
            price_changes.append(
                SelectionPriceChange(
                    selection_id=selection_id,
                    market_id=new_selection.get(
                        "mid"
                    ),
                    old=old_price,
                    new=new_price,
                )
            )

        old_visible = old_selection.get("visible")
        new_visible = new_selection.get("visible")

        if old_visible != new_visible:
            visibility_changes.append(
                SelectionVisibilityChange(
                    selection_id=selection_id,
                    market_id=new_selection.get(
                        "mid"
                    ),
                    old=old_visible,
                    new=new_visible,
                )
            )

    new_timeline_items = _find_new_timeline_items(
        previous.get("TimeLines"),
        current.get("TimeLines"),
    )

    match_ended = _is_match_ended_transition(
        previous_match,
        current_match,
    )

    match_ended_context = (
        _build_match_ended_context(
            previous_match,
            current_match,
        )
        if match_ended
        else ()
    )

    return LiveSnapshotDiff(
        match_changes=match_changes,
        price_changes=tuple(price_changes),
        visibility_changes=tuple(
            visibility_changes
        ),
        added_selections=added_selections,
        removed_selections=removed_selections,
        new_timeline_items=new_timeline_items,
        match_ended=match_ended,
        match_ended_context=match_ended_context,
    )


def _get_match(
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    match = snapshot.get("Match")

    if isinstance(match, dict):
        return match

    return {}


def _diff_match(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> tuple[FieldChange, ...]:
    changes: list[FieldChange] = []

    for field in MATCH_FIELDS:
        old = previous.get(field)
        new = current.get(field)

        if old != new:
            changes.append(
                FieldChange(
                    field=field,
                    old=old,
                    new=new,
                )
            )

    return tuple(changes)


def _match_meets_ended_criteria(
    match: dict[str, Any],
) -> bool:
    return (
        match.get("Status") == 3
        and match.get("BetStatus") == 0
        and match.get("EventStatus") == 40
    )


def _is_match_ended_transition(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> bool:
    """
    Detect the MATCH_ENDED transition by evaluating the complete
    current snapshot against the complete previous snapshot, rather
    than requiring Status/BetStatus/EventStatus to change together in
    the same notification. MyStake does not guarantee these fields
    change simultaneously; they may reach match-ended values across
    several successive live updates.
    """
    return _match_meets_ended_criteria(
        current
    ) and not _match_meets_ended_criteria(
        previous
    )


def _build_match_ended_context(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> tuple[FieldChange, ...]:
    return tuple(
        FieldChange(
            field=field,
            old=previous.get(field),
            new=current.get(field),
        )
        for field in MATCH_ENDED_FIELDS
    )


def _index_selections(
    value: Any,
) -> dict[int | str, dict[str, Any]]:
    result: dict[
        int | str,
        dict[str, Any],
    ] = {}

    for selection in _iter_dict_items(value):
        selection_id = selection.get("id")

        if selection_id is None:
            continue

        result[selection_id] = selection

    return result


def _iter_dict_items(
    value: Any,
):
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item
        return

    if isinstance(value, dict):
        for item in value.values():
            if isinstance(item, dict):
                yield item


def _find_new_timeline_items(
    previous: Any,
    current: Any,
) -> tuple[dict[str, Any], ...]:
    previous_items = list(
        _iter_dict_items(previous)
    )
    current_items = list(
        _iter_dict_items(current)
    )

    unmatched_previous = previous_items.copy()
    new_items: list[dict[str, Any]] = []

    for item in current_items:
        try:
            index = unmatched_previous.index(item)
        except ValueError:
            new_items.append(item)
        else:
            unmatched_previous.pop(index)

    return tuple(new_items)