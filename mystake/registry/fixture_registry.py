from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from mystake.models.fixture import Fixture

logger = logging.getLogger(__name__)


class FixtureChangeType(StrEnum):
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    METADATA_CHANGED = "METADATA_CHANGED"
    UNCHANGED = "UNCHANGED"


@dataclass(frozen=True)
class FixtureChange:
    game_id: int | str
    change_type: FixtureChangeType
    previous: Fixture | None
    current: Fixture | None


@dataclass(frozen=True)
class FixtureRegistryDiff:
    added: tuple[FixtureChange, ...]
    removed: tuple[FixtureChange, ...]
    metadata_changed: tuple[FixtureChange, ...]
    unchanged: tuple[FixtureChange, ...]

    @property
    def has_fixture_changes(self) -> bool:
        """
        True if any fixture was added, removed, or had its metadata
        changed. False when the refresh only confirmed `UNCHANGED`
        fixtures (a "no changes" refresh).
        """
        return bool(self.added or self.removed or self.metadata_changed)


_METADATA_FIELDS = (
    "sport",
    "region",
    "champ",
    "start_time",
    "team1",
    "team2",
    "sport_id",
    "region_id",
    "champ_id",
)


class FixtureRegistry:
    """
    In-memory registry of `Fixture` objects keyed by `game_id`.

    A registry disappearing entry is only ever classified as
    `REMOVED`; this registry has no concept of match lifecycle
    (e.g. `MATCH_ENDED`) and must never be used to infer one.
    """

    def __init__(self) -> None:
        self._fixtures: dict[int | str, Fixture] = {}

    def get(
        self,
        game_id: int | str,
    ) -> Fixture | None:
        return self._fixtures.get(game_id)

    def list_all(self) -> tuple[Fixture, ...]:
        return tuple(self._fixtures.values())

    def list_by_sport(
        self,
        sport,
    ) -> tuple[Fixture, ...]:
        return tuple(
            fixture for fixture in self._fixtures.values() if fixture.sport == sport
        )

    def refresh(
        self,
        fixtures: Iterable[Fixture],
    ) -> FixtureRegistryDiff:
        """
        Replace the registry contents with `fixtures` and return a
        diff against the previous contents. The registry is only
        mutated once the new fixtures have been fully indexed, so a
        caller that fails before calling `refresh` (e.g. an HTTP
        error or a decode error) naturally preserves the previous
        registry state - `refresh` itself never partially applies.
        """
        new_by_id = _index_fixtures(fixtures)
        old_by_id = self._fixtures

        old_ids = set(old_by_id)
        new_ids = set(new_by_id)

        added = tuple(
            FixtureChange(
                game_id=game_id,
                change_type=FixtureChangeType.ADDED,
                previous=None,
                current=new_by_id[game_id],
            )
            for game_id in sorted(
                new_ids - old_ids,
                key=str,
            )
        )

        removed = tuple(
            FixtureChange(
                game_id=game_id,
                change_type=FixtureChangeType.REMOVED,
                previous=old_by_id[game_id],
                current=None,
            )
            for game_id in sorted(
                old_ids - new_ids,
                key=str,
            )
        )

        metadata_changed: list[FixtureChange] = []
        unchanged: list[FixtureChange] = []

        for game_id in sorted(
            old_ids & new_ids,
            key=str,
        ):
            old_fixture = old_by_id[game_id]
            new_fixture = new_by_id[game_id]

            if _metadata_key(old_fixture) != _metadata_key(new_fixture):
                metadata_changed.append(
                    FixtureChange(
                        game_id=game_id,
                        change_type=(FixtureChangeType.METADATA_CHANGED),
                        previous=old_fixture,
                        current=new_fixture,
                    )
                )
            else:
                unchanged.append(
                    FixtureChange(
                        game_id=game_id,
                        change_type=FixtureChangeType.UNCHANGED,
                        previous=old_fixture,
                        current=new_fixture,
                    )
                )

        self._fixtures = new_by_id

        return FixtureRegistryDiff(
            added=added,
            removed=removed,
            metadata_changed=tuple(metadata_changed),
            unchanged=tuple(unchanged),
        )


def _metadata_key(
    fixture: Fixture,
) -> tuple:
    return tuple(getattr(fixture, field) for field in _METADATA_FIELDS)


def _index_fixtures(
    fixtures: Iterable[Fixture],
) -> dict[int | str, Fixture]:
    indexed: dict[int | str, Fixture] = {}

    for fixture in fixtures:
        if fixture.game_id is None:
            logger.warning(
                "Skipping fixture with missing game_id raw=%s",
                fixture.raw,
            )
            continue

        if fixture.game_id in indexed:
            logger.warning(
                "Duplicate GameId=%s encountered during "
                "registry refresh; keeping the last occurrence",
                fixture.game_id,
            )

        indexed[fixture.game_id] = fixture

    return indexed
