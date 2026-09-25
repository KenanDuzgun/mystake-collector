"""
Phase 2 fixture discovery CLI - runs prematch and live fixture
discovery without opening a browser, and demonstrates a registry
refresh (added/removed/changed/unchanged).

Usage:

    uv run python discover_fixtures.py

This performs real network requests against MyStake's API
(`getheader/en` and the `live/headernew/en` cache resource). It does
not call `gamefull`/market hydration for any fixture - discovery only.
"""

import logging

from mystake.pipeline.live_discovery import LiveFixtureDiscovery
from mystake.pipeline.prematch_discovery import PrematchFixtureDiscovery
from mystake.registry.fixture_registry import FixtureRegistry, FixtureRegistryDiff
from mystake.sources.cache.client import MystakeCacheClient
from mystake.sources.http.client import MystakeHttpClient

SAMPLE_SIZE = 5


def print_header(title: str) -> None:
    print()
    print(title)
    print("-" * 50)


def print_fixtures_by_sport(registry: FixtureRegistry) -> None:
    fixtures = registry.list_all()

    print(f"Total fixtures : {len(fixtures)}")

    counts: dict[object, int] = {}

    for fixture in fixtures:
        counts[fixture.sport] = counts.get(fixture.sport, 0) + 1

    if counts:
        print()
        print("Fixtures by sport:")

        for sport, count in sorted(
            counts.items(),
            key=lambda pair: (-pair[1], str(pair[0])),
        ):
            label = "Unknown" if sport is None else str(sport)
            print(f"{label:<15}: {count}")

    if fixtures:
        print()
        print(f"Sample fixtures (showing up to {SAMPLE_SIZE}):")

        for fixture in fixtures[:SAMPLE_SIZE]:
            print(
                f"  game_id={fixture.game_id!r} "
                f"sport={fixture.sport!r} "
                f"region={fixture.region!r} "
                f"champ={fixture.champ!r} "
                f"start_time={fixture.start_time!r} "
                f"t1={fixture.team1!r} t2={fixture.team2!r}"
            )


def print_registry_diff(diff: FixtureRegistryDiff | None) -> None:
    if diff is None:
        print(
            "FAILED - discovery request or decode failed; "
            "previous registry state (if any) was preserved. "
            "See logs above for details."
        )
        return

    print(f"Added          : {len(diff.added)}")
    print(f"Removed        : {len(diff.removed)}")
    print(f"Changed        : {len(diff.metadata_changed)}")
    print(f"Unchanged      : {len(diff.unchanged)}")


def main() -> None:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    http_client = MystakeHttpClient()
    cache_client = MystakeCacheClient()

    prematch_discovery = PrematchFixtureDiscovery(http_client=http_client)
    live_discovery = LiveFixtureDiscovery(cache_client=cache_client)

    try:
        print_header("PREMATCH DISCOVERY")

        prematch_registry = prematch_discovery.registry

        prematch_diff = prematch_discovery.refresh()

        if prematch_diff is None:
            print(
                "FAILED - could not discover prematch fixtures "
                "(getheader/en request or decode failed). "
                "See logs above for details."
            )
        else:
            sports = {f.sport for f in prematch_registry.list_all()}
            print(f"Total sports   : {len(sports)}")
            print_fixtures_by_sport(prematch_registry)

        print_header("LIVE DISCOVERY")

        live_registry = live_discovery.registry
        live_diff = live_discovery.refresh()

        if live_diff is None:
            print(
                "FAILED - could not discover live fixtures "
                "(live/headernew/en request or decode failed). "
                "See logs above for details."
            )
        else:
            print_fixtures_by_sport(live_registry)

        print_header("REGISTRY REFRESH")

        refresh_diff = prematch_discovery.refresh()
        print_registry_diff(refresh_diff)

    finally:
        http_client.close()
        cache_client.close()


if __name__ == "__main__":
    main()
