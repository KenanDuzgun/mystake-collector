from mystake.registry.live_game_registry import (
    LiveGameRegistry,
    LiveLifecycleState,
)


def snapshot(
    game_id, score="0:0", gmk=None, status=None, bet_status=None, event_status=None
):
    match = {"GameID": game_id, "Score": score}
    if status is not None:
        match["Status"] = status
    if bet_status is not None:
        match["BetStatus"] = bet_status
    if event_status is not None:
        match["EventStatus"] = event_status
    return {
        "Match": match,
        "gmk": gmk if gmk is not None else [],
    }


def terminal_snapshot(game_id, score="2:1"):
    return snapshot(game_id, score=score, status=3, bet_status=0, event_status=40)


def suspended_snapshot(game_id, score="0:0"):
    """Temporary betting suspension - not a terminal state on its own."""
    return snapshot(game_id, score=score, status=1, bet_status=0, event_status=10)


def test_tracked_game_ids_deduplicated_and_ordered():
    registry = LiveGameRegistry([1, 2, 1])

    assert registry.tracked_game_ids() == (1, 2)


def test_initial_state_has_no_snapshot():
    registry = LiveGameRegistry([1])

    state = registry.get(1)

    assert state is not None
    assert state.snapshot is None
    assert state.notification_count == 0


def test_first_notification_is_initial_with_no_diff():
    registry = LiveGameRegistry([1])

    outcome = registry.apply_notification(1, snapshot(1))

    assert outcome.is_initial is True
    assert outcome.duplicate is False
    assert outcome.diff is None
    assert registry.get(1).snapshot is not None
    assert registry.get(1).notification_count == 1


def test_identical_second_notification_is_deduplicated():
    registry = LiveGameRegistry([1])
    registry.apply_notification(1, snapshot(1))

    outcome = registry.apply_notification(1, snapshot(1))

    assert outcome.is_initial is False
    assert outcome.duplicate is True
    assert outcome.diff is None
    assert registry.get(1).notification_count == 2


def test_score_change_reflected_in_diff():
    registry = LiveGameRegistry([1])
    registry.apply_notification(1, snapshot(1, score="0:0"))

    outcome = registry.apply_notification(1, snapshot(1, score="1:0"))

    assert outcome.duplicate is False
    assert outcome.diff.has_changes is True
    assert outcome.diff.match_changes[0].old == "0:0"
    assert outcome.diff.match_changes[0].new == "1:0"


def test_price_change_reflected_in_diff():
    registry = LiveGameRegistry([1])
    registry.apply_notification(
        1, snapshot(1, gmk=[{"id": 10, "mid": 5, "v": 1.8, "visible": True}])
    )

    outcome = registry.apply_notification(
        1, snapshot(1, gmk=[{"id": 10, "mid": 5, "v": 2.0, "visible": True}])
    )

    assert outcome.diff.price_changes[0].old == 1.8
    assert outcome.diff.price_changes[0].new == 2.0


def test_record_failure_preserves_previous_snapshot():
    registry = LiveGameRegistry([1])
    registry.apply_notification(1, snapshot(1))
    previous_snapshot = registry.get(1).snapshot

    registry.record_failure(1, error="cache fetch failed")

    assert registry.get(1).snapshot is previous_snapshot
    assert registry.get(1).last_error == "cache fetch failed"


def test_untracked_game_id_is_ignored():
    registry = LiveGameRegistry([1])

    outcome = registry.apply_notification(999, snapshot(999))

    assert outcome is None
    assert registry.get(999) is None


def test_multiple_tracked_games_are_isolated():
    registry = LiveGameRegistry([1, 2])

    registry.apply_notification(1, snapshot(1, score="0:0"))
    registry.apply_notification(2, snapshot(2, score="0:0"))

    registry.apply_notification(1, snapshot(1, score="1:0"))

    assert registry.get(1).snapshot["Match"]["Score"] == "1:0"
    assert registry.get(2).snapshot["Match"]["Score"] == "0:0"
    assert registry.get(1).notification_count == 2
    assert registry.get(2).notification_count == 1

    registry.record_failure(2, error="boom")

    assert registry.get(2).last_error == "boom"
    assert registry.get(1).last_error is None


# --- Phase 4D: lifecycle management ---


def test_initial_lifecycle_state_is_active():
    registry = LiveGameRegistry([1])

    assert registry.get(1).lifecycle_state is LiveLifecycleState.ACTIVE
    assert registry.get(1).is_finalized is False
    assert registry.active_game_ids() == (1,)
    assert registry.finalized_game_ids() == ()


def test_terminal_transition_finalizes_game():
    registry = LiveGameRegistry([1])
    registry.apply_notification(1, snapshot(1, status=1, bet_status=1, event_status=10))

    outcome = registry.apply_notification(1, terminal_snapshot(1))

    assert outcome.became_terminal is True
    assert outcome.diff.match_ended is True
    assert registry.get(1).lifecycle_state is LiveLifecycleState.TERMINAL
    assert registry.get(1).is_finalized is True
    assert registry.get(1).finalized_at is not None
    assert registry.is_finalized(1) is True
    assert registry.active_game_ids() == ()
    assert registry.finalized_game_ids() == (1,)


def test_non_terminal_status_change_keeps_game_active():
    registry = LiveGameRegistry([1])
    registry.apply_notification(1, snapshot(1, status=1, bet_status=1, event_status=10))

    outcome = registry.apply_notification(
        1, snapshot(1, score="1:0", status=1, bet_status=1, event_status=10)
    )

    assert outcome.became_terminal is False
    assert registry.get(1).lifecycle_state is LiveLifecycleState.ACTIVE


def test_temporary_betting_suspension_does_not_finalize():
    """BetStatus=0 alone (temporary suspension) must not be mistaken for
    match completion - only the full Status=3/BetStatus=0/EventStatus=40
    combination is treated as terminal."""
    registry = LiveGameRegistry([1])
    registry.apply_notification(1, snapshot(1, status=1, bet_status=1, event_status=10))

    outcome = registry.apply_notification(1, suspended_snapshot(1))

    assert outcome.became_terminal is False
    assert registry.get(1).lifecycle_state is LiveLifecycleState.ACTIVE
    assert registry.is_finalized(1) is False


def test_duplicate_terminal_notification_does_not_refinalize():
    registry = LiveGameRegistry([1])
    registry.apply_notification(1, snapshot(1, status=1, bet_status=1, event_status=10))
    first = registry.apply_notification(1, terminal_snapshot(1))
    finalized_at = registry.get(1).finalized_at

    second = registry.apply_notification(1, terminal_snapshot(1, score="2:1"))

    assert first.became_terminal is True
    assert second.ignored_after_finalization is True
    assert second.became_terminal is False
    assert registry.get(1).finalized_at == finalized_at


def test_late_notification_after_finalization_preserves_final_snapshot():
    registry = LiveGameRegistry([1])
    registry.apply_notification(1, snapshot(1, status=1, bet_status=1, event_status=10))
    registry.apply_notification(1, terminal_snapshot(1, score="2:1"))
    final_snapshot = registry.get(1).snapshot

    outcome = registry.apply_notification(1, snapshot(1, score="3:1"))

    assert outcome.ignored_after_finalization is True
    assert outcome.diff is None
    assert registry.get(1).snapshot is final_snapshot
    assert registry.get(1).snapshot["Match"]["Score"] == "2:1"


def test_record_failure_after_finalization_is_ignored():
    registry = LiveGameRegistry([1])
    registry.apply_notification(1, snapshot(1, status=1, bet_status=1, event_status=10))
    registry.apply_notification(1, terminal_snapshot(1))
    final_snapshot = registry.get(1).snapshot

    registry.record_failure(1, error="stale in-flight failure")

    assert registry.get(1).snapshot is final_snapshot
    assert registry.get(1).last_error is None
    assert registry.get(1).lifecycle_state is LiveLifecycleState.TERMINAL


def test_finalization_of_one_game_does_not_affect_another():
    registry = LiveGameRegistry([1, 2])
    registry.apply_notification(1, snapshot(1, status=1, bet_status=1, event_status=10))
    registry.apply_notification(2, snapshot(2, status=1, bet_status=1, event_status=10))

    registry.apply_notification(1, terminal_snapshot(1))

    assert registry.get(1).lifecycle_state is LiveLifecycleState.TERMINAL
    assert registry.get(2).lifecycle_state is LiveLifecycleState.ACTIVE
    assert registry.active_game_ids() == (2,)
    assert registry.finalized_game_ids() == (1,)


def test_missing_status_fields_do_not_trigger_terminal():
    registry = LiveGameRegistry([1])
    registry.apply_notification(1, snapshot(1))

    outcome = registry.apply_notification(1, snapshot(1, score="1:0"))

    assert outcome.became_terminal is False
    assert registry.get(1).lifecycle_state is LiveLifecycleState.ACTIVE


# --- Phase 4D: fixture disappearance reconciliation ---


def test_reconcile_marks_missing_active_game_unknown():
    registry = LiveGameRegistry([1, 2])

    result = registry.reconcile_discovery([2])

    assert result.newly_unknown == (1,)
    assert result.still_unknown == ()
    assert result.recovered == ()
    assert registry.get(1).lifecycle_state is LiveLifecycleState.UNKNOWN
    assert registry.get(2).lifecycle_state is LiveLifecycleState.ACTIVE


def test_reconcile_keeps_tracking_unknown_game_active_flag_off():
    """Disappearance must not remove the game from tracking or finalize it."""
    registry = LiveGameRegistry([1])
    registry.reconcile_discovery([])

    assert registry.get(1) is not None
    assert registry.get(1).is_finalized is False
    assert 1 in registry.tracked_game_ids()
    assert 1 in registry.active_game_ids()


def test_reconcile_still_unknown_increments_streak():
    registry = LiveGameRegistry([1])
    registry.reconcile_discovery([])

    result = registry.reconcile_discovery([])

    assert result.still_unknown == (1,)
    assert registry.get(1).missing_from_discovery_streak == 2


def test_reconcile_recovers_unknown_game_to_active():
    registry = LiveGameRegistry([1])
    registry.reconcile_discovery([])
    assert registry.get(1).lifecycle_state is LiveLifecycleState.UNKNOWN

    result = registry.reconcile_discovery([1])

    assert result.recovered == (1,)
    assert registry.get(1).lifecycle_state is LiveLifecycleState.ACTIVE
    assert registry.get(1).missing_from_discovery_since is None


def test_real_notification_recovers_unknown_game_to_active():
    registry = LiveGameRegistry([1])
    registry.reconcile_discovery([])
    assert registry.get(1).lifecycle_state is LiveLifecycleState.UNKNOWN

    registry.apply_notification(1, snapshot(1, score="1:0"))

    assert registry.get(1).lifecycle_state is LiveLifecycleState.ACTIVE
    assert registry.get(1).missing_from_discovery_since is None


def test_reconcile_never_touches_finalized_game():
    registry = LiveGameRegistry([1])
    registry.apply_notification(1, snapshot(1, status=1, bet_status=1, event_status=10))
    registry.apply_notification(1, terminal_snapshot(1))

    result = registry.reconcile_discovery([])

    assert result.newly_unknown == ()
    assert registry.get(1).lifecycle_state is LiveLifecycleState.TERMINAL
