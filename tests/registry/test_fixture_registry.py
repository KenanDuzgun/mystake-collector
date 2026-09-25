from mystake.models.fixture import Fixture
from mystake.registry.fixture_registry import (
    FixtureChangeType,
    FixtureRegistry,
)


def make_fixture(
    game_id,
    sport="Soccer",
    start_time=1,
    team1="A",
    team2="B",
):
    return Fixture(
        game_id=game_id,
        sport=sport,
        region="England",
        champ="Premier League",
        start_time=start_time,
        team1=team1,
        team2=team2,
        raw={"ID": game_id},
    )


def test_initial_discovery_reports_everything_as_added():
    registry = FixtureRegistry()

    diff = registry.refresh([make_fixture(1), make_fixture(2)])

    assert {change.game_id for change in diff.added} == {1, 2}
    assert diff.removed == ()
    assert diff.metadata_changed == ()
    assert diff.unchanged == ()
    assert diff.has_fixture_changes is True


def test_lookup_by_game_id():
    registry = FixtureRegistry()
    fixture = make_fixture(1)

    registry.refresh([fixture])

    assert registry.get(1) is fixture
    assert registry.get(999) is None


def test_list_all():
    registry = FixtureRegistry()

    registry.refresh([make_fixture(1), make_fixture(2)])

    assert {f.game_id for f in registry.list_all()} == {1, 2}


def test_list_by_sport():
    registry = FixtureRegistry()

    registry.refresh(
        [
            make_fixture(1, sport="Soccer"),
            make_fixture(2, sport="Basketball"),
        ]
    )

    soccer = registry.list_by_sport("Soccer")

    assert len(soccer) == 1
    assert soccer[0].game_id == 1


def test_added_fixture_detected_on_refresh():
    registry = FixtureRegistry()
    registry.refresh([make_fixture(1)])

    diff = registry.refresh([make_fixture(1), make_fixture(2)])

    assert {c.game_id for c in diff.added} == {2}
    assert diff.removed == ()
    assert diff.metadata_changed == ()
    assert {c.game_id for c in diff.unchanged} == {1}


def test_removed_fixture_detected_on_refresh():
    registry = FixtureRegistry()
    registry.refresh([make_fixture(1), make_fixture(2)])

    diff = registry.refresh([make_fixture(1)])

    assert diff.added == ()
    assert {c.game_id for c in diff.removed} == {2}
    assert diff.removed[0].change_type == FixtureChangeType.REMOVED
    assert diff.removed[0].current is None


def test_removed_fixture_is_never_labeled_match_ended():
    """
    Regression: a fixture disappearing from getheader must only ever
    be classified as REMOVED, never inferred as MATCH_ENDED.
    """
    registry = FixtureRegistry()
    registry.refresh([make_fixture(1)])

    diff = registry.refresh([])

    assert diff.removed[0].change_type == FixtureChangeType.REMOVED
    assert diff.removed[0].change_type != "MATCH_ENDED"


def test_metadata_change_detected_on_refresh():
    registry = FixtureRegistry()
    registry.refresh([make_fixture(1, start_time=100)])

    diff = registry.refresh([make_fixture(1, start_time=200)])

    assert diff.added == ()
    assert diff.removed == ()
    assert len(diff.metadata_changed) == 1
    assert diff.metadata_changed[0].previous.start_time == 100
    assert diff.metadata_changed[0].current.start_time == 200
    assert diff.unchanged == ()


def test_unchanged_fixture_detected_on_refresh():
    registry = FixtureRegistry()
    fixture = make_fixture(1)
    registry.refresh([fixture])

    diff = registry.refresh([make_fixture(1)])

    assert diff.added == ()
    assert diff.removed == ()
    assert diff.metadata_changed == ()
    assert {c.game_id for c in diff.unchanged} == {1}
    assert diff.has_fixture_changes is False


def test_successful_refresh_with_no_changes():
    registry = FixtureRegistry()
    registry.refresh([make_fixture(1), make_fixture(2)])

    diff = registry.refresh([make_fixture(1), make_fixture(2)])

    assert diff.added == ()
    assert diff.removed == ()
    assert diff.metadata_changed == ()
    assert len(diff.unchanged) == 2


def test_duplicate_game_id_keeps_last_occurrence():
    registry = FixtureRegistry()

    diff = registry.refresh(
        [
            make_fixture(1, start_time=100),
            make_fixture(1, start_time=200),
        ]
    )

    assert len(diff.added) == 1
    assert registry.get(1).start_time == 200
    assert len(registry.list_all()) == 1


def test_fixture_with_missing_game_id_is_skipped():
    registry = FixtureRegistry()

    fixture = make_fixture(None)

    diff = registry.refresh([fixture])

    assert diff.added == ()
    assert registry.list_all() == ()


def test_empty_refresh_removes_all_fixtures():
    registry = FixtureRegistry()
    registry.refresh([make_fixture(1)])

    diff = registry.refresh([])

    assert len(diff.removed) == 1
    assert registry.list_all() == ()
