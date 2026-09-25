from mystake.models.snapshot import parse_prematch_snapshot
from mystake.registry.game_snapshot_registry import GameSnapshotRegistry


def snapshot(game_id, ev):
    return parse_prematch_snapshot({"id": game_id, "ev": ev})


def test_tracked_game_ids_deduplicated_and_ordered():
    registry = GameSnapshotRegistry([1, 2, 1])

    assert registry.tracked_game_ids() == (1, 2)


def test_initial_state_has_no_snapshot():
    registry = GameSnapshotRegistry([1])

    state = registry.get(1)

    assert state is not None
    assert state.snapshot is None


def test_first_successful_fetch_is_initial_with_no_diff():
    registry = GameSnapshotRegistry([1])

    outcome = registry.apply_fetch_result(
        1, snapshot(1, {"55": {"2001": {"coef": 1.8}}})
    )

    assert outcome.is_initial is True
    assert outcome.failed is False
    assert outcome.diff is None
    assert registry.get(1).snapshot is not None


def test_second_fetch_with_no_change_produces_unchanged_diff():
    registry = GameSnapshotRegistry([1])
    registry.apply_fetch_result(1, snapshot(1, {"55": {"2001": {"coef": 1.8}}}))

    outcome = registry.apply_fetch_result(
        1, snapshot(1, {"55": {"2001": {"coef": 1.8}}})
    )

    assert outcome.is_initial is False
    assert outcome.failed is False
    assert outcome.diff.has_changes is False


def test_price_change_reflected_in_diff():
    registry = GameSnapshotRegistry([1])
    registry.apply_fetch_result(1, snapshot(1, {"55": {"2001": {"coef": 1.8}}}))

    outcome = registry.apply_fetch_result(
        1, snapshot(1, {"55": {"2001": {"coef": 2.0}}})
    )

    assert outcome.diff.has_changes is True
    assert outcome.diff.price_changes[0].old == 1.8
    assert outcome.diff.price_changes[0].new == 2.0


def test_failed_fetch_preserves_previous_snapshot():
    registry = GameSnapshotRegistry([1])
    registry.apply_fetch_result(1, snapshot(1, {"55": {"2001": {"coef": 1.8}}}))
    previous_snapshot = registry.get(1).snapshot

    outcome = registry.apply_fetch_result(1, None, error="timeout")

    assert outcome.failed is True
    assert outcome.snapshot is previous_snapshot
    assert registry.get(1).snapshot is previous_snapshot
    assert registry.get(1).last_error == "timeout"


def test_failed_fetch_before_any_success_leaves_no_snapshot():
    registry = GameSnapshotRegistry([1])

    outcome = registry.apply_fetch_result(1, None, error="boom")

    assert outcome.failed is True
    assert outcome.snapshot is None
    assert registry.get(1).snapshot is None


def test_untracked_game_id_is_ignored():
    registry = GameSnapshotRegistry([1])

    outcome = registry.apply_fetch_result(999, snapshot(999, {}))

    assert outcome is None
    assert registry.get(999) is None


def test_multiple_tracked_games_are_isolated():
    registry = GameSnapshotRegistry([1, 2])

    registry.apply_fetch_result(1, snapshot(1, {"55": {"2001": {"coef": 1.8}}}))
    registry.apply_fetch_result(2, snapshot(2, {"66": {"3001": {"coef": 2.5}}}))

    assert registry.get(1).snapshot.game_id == 1
    assert registry.get(2).snapshot.game_id == 2
    assert registry.get(1).snapshot.markets[0].id == "55"
    assert registry.get(2).snapshot.markets[0].id == "66"

    # A failure on one tracked game must not affect the other's state.
    registry.apply_fetch_result(1, None, error="boom")

    assert registry.get(1).last_error == "boom"
    assert registry.get(2).last_error is None
    assert registry.get(2).snapshot.markets[0].id == "66"
