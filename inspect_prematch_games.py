from __future__ import annotations

import json
import logging
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import httpx

from mystake.pipeline.notification_processor import NotificationProcessor
from mystake.sources.cache.client import MystakeCacheClient
from mystake.sources.mqtt.client import MystakeMqttClient


MQTT_TOPIC = "prematch/games"

PREMATCH_API_BASE_URL = "https://analytics-sp.googleserv.tech"
PREMATCH_CONTEXT_ID = 28
PREMATCH_LANGUAGE = "fr"

PRINT_STATS_EVERY_NOTIFICATIONS = 25


# ======================================================================================
# STATS
# ======================================================================================


@dataclass
class PrematchStats:
    notifications: int = 0
    duplicate_notifications: int = 0

    delta_batches: int = 0
    delta_games: int = 0

    full_bootstraps: int = 0

    delta_merges: int = 0
    count_matches: int = 0
    count_mismatches: int = 0

    full_resyncs: int = 0

    logical_changes: int = 0
    no_op_updates: int = 0

    price_changes: int = 0
    lock_changes: int = 0

    added_markets: int = 0
    removed_markets: int = 0

    added_selections: int = 0
    removed_selections: int = 0


@dataclass
class DiffSummary:
    has_changes: bool = False

    price_changes: int = 0
    lock_changes: int = 0

    added_markets: int = 0
    removed_markets: int = 0

    added_selections: int = 0
    removed_selections: int = 0


def print_stats(stats: PrematchStats) -> None:
    print()
    print("=" * 100)
    print("PREMATCH SOAK TEST STATS")
    print("=" * 100)

    print(
        f"Notifications            : "
        f"{stats.notifications}"
    )

    print(
        f"Duplicate notifications  : "
        f"{stats.duplicate_notifications}"
    )

    print(
        f"Delta batches            : "
        f"{stats.delta_batches}"
    )

    print(
        f"Delta games              : "
        f"{stats.delta_games}"
    )

    print(
        f"Full bootstraps          : "
        f"{stats.full_bootstraps}"
    )

    print(
        f"Delta merges             : "
        f"{stats.delta_merges}"
    )

    print(
        f"Count matches            : "
        f"{stats.count_matches}"
    )

    print(
        f"Count mismatches         : "
        f"{stats.count_mismatches}"
    )

    print(
        f"Full resyncs             : "
        f"{stats.full_resyncs}"
    )

    print(
        f"Logical changes          : "
        f"{stats.logical_changes}"
    )

    print(
        f"No-op updates             : "
        f"{stats.no_op_updates}"
    )

    print(
        f"Price changes            : "
        f"{stats.price_changes}"
    )

    print(
        f"Lock changes             : "
        f"{stats.lock_changes}"
    )

    print(
        f"Added markets            : "
        f"{stats.added_markets}"
    )

    print(
        f"Removed markets          : "
        f"{stats.removed_markets}"
    )

    print(
        f"Added selections         : "
        f"{stats.added_selections}"
    )

    print(
        f"Removed selections       : "
        f"{stats.removed_selections}"
    )

    if stats.delta_merges > 0:
        success_rate = (
            stats.count_matches
            / stats.delta_merges
            * 100
        )

        print()
        print(
            f"Direct merge count-match : "
            f"{success_rate:.2f}%"
        )

    print("=" * 100)


# ======================================================================================
# JSON HELPERS
# ======================================================================================


def canonical_json(data: Any) -> str:
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def decode_json_string(
    value: Any,
    *,
    expected_type: type,
) -> Any:
    if isinstance(value, expected_type):
        return value

    if not isinstance(value, str):
        raise ValueError(
            f"Expected {expected_type.__name__} or JSON string, "
            f"got {type(value).__name__}"
        )

    decoded = json.loads(value)

    if not isinstance(decoded, expected_type):
        raise ValueError(
            f"Decoded JSON expected {expected_type.__name__}, "
            f"got {type(decoded).__name__}"
        )

    return decoded


def decode_outer_payload(
    response: httpx.Response,
) -> dict[str, Any]:
    data = response.json()

    if isinstance(data, str):
        data = json.loads(data)

    if not isinstance(data, dict):
        raise ValueError(
            "Unexpected API response type: "
            f"{type(data).__name__}"
        )

    return data


# ======================================================================================
# PREMATCH HTTP API
# ======================================================================================


def build_gameall_url(
    game_ids: list[int | str],
) -> str:
    games_param = "," + ",".join(
        str(game_id)
        for game_id in game_ids
    )

    return (
        f"{PREMATCH_API_BASE_URL}"
        f"/api/prematch/getprematchgameall/"
        f"{PREMATCH_LANGUAGE}/"
        f"{PREMATCH_CONTEXT_ID}/"
        f"?games={games_param}"
    )


def build_gamefull_url(
    game_id: int | str,
) -> str:
    return (
        f"{PREMATCH_API_BASE_URL}"
        f"/api/prematch/getprematchgamefull/"
        f"{PREMATCH_CONTEXT_ID}/"
        f"{game_id}"
    )


def fetch_gameall(
    client: httpx.Client,
    game_ids: list[int | str],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    if not game_ids:
        return [], []

    url = build_gameall_url(game_ids)

    logging.info(
        "Fetching prematch DELTA batch games=%s",
        len(game_ids),
    )

    response = client.get(url)
    response.raise_for_status()

    payload = decode_outer_payload(response)

    games = decode_json_string(
        payload.get("game", "[]"),
        expected_type=list,
    )

    teams = decode_json_string(
        payload.get("teams", "[]"),
        expected_type=list,
    )

    return games, teams


def fetch_gamefull(
    client: httpx.Client,
    game_id: int | str,
) -> dict[str, Any]:
    url = build_gamefull_url(game_id)

    logging.info(
        "Fetching prematch FULL snapshot gameId=%s",
        game_id,
    )

    response = client.get(url)
    response.raise_for_status()

    payload = decode_outer_payload(response)

    return decode_json_string(
        payload.get("game"),
        expected_type=dict,
    )


# ======================================================================================
# TEAM METADATA
# ======================================================================================


def update_team_map(
    team_map: dict[int | str, str],
    teams: list[dict[str, Any]],
) -> None:
    for team in teams:
        if not isinstance(team, dict):
            continue

        team_id = team.get("ID")
        team_name = team.get("Name")

        if team_id is None:
            continue

        if isinstance(team_name, str):
            team_map[team_id] = team_name


def team_name(
    team_map: dict[int | str, str],
    team_id: Any,
) -> str:
    return team_map.get(
        team_id,
        f"UNKNOWN TEAM {team_id}",
    )


# ======================================================================================
# STATE HELPERS
# ======================================================================================


def get_markets(
    game: dict[str, Any],
) -> dict[str, Any]:
    markets = game.get("ev")

    if not isinstance(markets, dict):
        return {}

    return markets


def count_markets(
    game: dict[str, Any],
) -> int:
    return len(get_markets(game))


def count_selections(
    game: dict[str, Any],
) -> int:
    return sum(
        len(selections)
        for selections in get_markets(game).values()
        if isinstance(selections, dict)
    )


# ======================================================================================
# DELTA MERGE
# ======================================================================================


def merge_game_delta(
    current_state: dict[str, Any],
    delta: dict[str, Any],
) -> dict[str, Any]:
    """
    Current evidence-based working model:

    - getprematchgameall is partial at game level.
    - A market missing from delta is preserved.
    - A market present in delta replaces that complete local market value.
    - Top-level scalar fields present in delta replace existing values.
    - pc mismatch triggers gamefull resync.
    """

    merged = deepcopy(current_state)

    for key, value in delta.items():
        if key == "ev":
            continue

        merged[key] = deepcopy(value)

    delta_markets = delta.get("ev")

    if not isinstance(delta_markets, dict):
        return merged

    merged_markets = merged.get("ev")

    if not isinstance(merged_markets, dict):
        merged_markets = {}
        merged["ev"] = merged_markets

    for market_id, delta_selections in delta_markets.items():
        merged_markets[market_id] = deepcopy(
            delta_selections
        )

    return merged


# ======================================================================================
# DIFF
# ======================================================================================


def diff_scalar_fields(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    ignored_fields = {"ev"}

    keys = (
        (set(previous) - ignored_fields)
        | (set(current) - ignored_fields)
    )

    changes: dict[str, dict[str, Any]] = {}

    for key in sorted(keys):
        old = previous.get(key)
        new = current.get(key)

        if old != new:
            changes[key] = {
                "old": old,
                "new": new,
            }

    return changes


def diff_full_markets(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    previous_markets = get_markets(previous)
    current_markets = get_markets(current)

    old_market_ids = set(previous_markets)
    new_market_ids = set(current_markets)

    added_markets = sorted(
        new_market_ids - old_market_ids
    )

    removed_markets = sorted(
        old_market_ids - new_market_ids
    )

    changed_markets: dict[str, Any] = {}

    for market_id in sorted(
        old_market_ids & new_market_ids
    ):
        old_selections = previous_markets[
            market_id
        ]

        new_selections = current_markets[
            market_id
        ]

        if not isinstance(old_selections, dict):
            old_selections = {}

        if not isinstance(new_selections, dict):
            new_selections = {}

        old_ids = set(old_selections)
        new_ids = set(new_selections)

        added_selections = sorted(
            new_ids - old_ids
        )

        removed_selections = sorted(
            old_ids - new_ids
        )

        changed_selections: dict[
            str,
            dict[str, Any],
        ] = {}

        for selection_id in sorted(
            old_ids & new_ids
        ):
            old_selection = old_selections[
                selection_id
            ]

            new_selection = new_selections[
                selection_id
            ]

            if old_selection == new_selection:
                continue

            if (
                not isinstance(old_selection, dict)
                or not isinstance(new_selection, dict)
            ):
                changed_selections[
                    selection_id
                ] = {
                    "__value__": {
                        "old": old_selection,
                        "new": new_selection,
                    }
                }
                continue

            field_changes: dict[
                str,
                dict[str, Any],
            ] = {}

            fields = (
                set(old_selection)
                | set(new_selection)
            )

            for field in sorted(fields):
                old_value = old_selection.get(
                    field
                )

                new_value = new_selection.get(
                    field
                )

                if old_value != new_value:
                    field_changes[field] = {
                        "old": old_value,
                        "new": new_value,
                    }

            if field_changes:
                changed_selections[
                    selection_id
                ] = field_changes

        if (
            added_selections
            or removed_selections
            or changed_selections
        ):
            changed_markets[
                market_id
            ] = {
                "added_selections":
                    added_selections,
                "removed_selections":
                    removed_selections,
                "changed_selections":
                    changed_selections,
            }

    return {
        "added_markets": added_markets,
        "removed_markets": removed_markets,
        "changed_markets": changed_markets,
    }


# ======================================================================================
# OUTPUT
# ======================================================================================


def print_game_summary(
    game: dict[str, Any],
    team_map: dict[int | str, str],
) -> None:
    team1_id = game.get("t1")
    team2_id = game.get("t2")

    print()
    print("-" * 100)

    print(
        f"GameId          : "
        f"{game.get('id')}"
    )

    print(
        "Match           : "
        f"{team_name(team_map, team1_id)}"
        " vs "
        f"{team_name(team_map, team2_id)}"
    )

    print(
        f"Team IDs        : "
        f"{team1_id} / {team2_id}"
    )

    print(
        f"Start           : "
        f"{game.get('st')}"
    )

    print(
        f"Sport           : "
        f"{game.get('sport')}"
    )

    print(
        f"Region          : "
        f"{game.get('region')}"
    )

    print(
        f"Champ/Ch        : "
        f"{game.get('ch')}"
    )

    print(
        f"Local markets   : "
        f"{count_markets(game)}"
    )

    print(
        f"Local selections: "
        f"{count_selections(game)}"
    )

    print(
        f"Server mc       : "
        f"{game.get('mc')}"
    )

    print(
        f"Server pc       : "
        f"{game.get('pc')}"
    )

    print(
        f"Updated         : "
        f"{game.get('up')}"
    )

    print(
        f"Visible         : "
        f"{game.get('vis')}"
    )

    print(
        f"Has stream      : "
        f"{game.get('hasstream')}"
    )


def print_delta_summary(
    delta: dict[str, Any],
) -> None:
    print()
    print("DELTA PAYLOAD")
    print("-" * 100)

    print(
        f"delta markets    : "
        f"{count_markets(delta)}"
    )

    print(
        f"delta selections : "
        f"{count_selections(delta)}"
    )

    print(
        f"delta up         : "
        f"{delta.get('up')}"
    )

    print(
        f"delta mc         : "
        f"{delta.get('mc')}"
    )

    print(
        f"delta pc         : "
        f"{delta.get('pc')}"
    )


def print_full_state_diff(
    previous: dict[str, Any],
    current: dict[str, Any],
    *,
    source: str,
) -> DiffSummary:
    game_id = current.get("id")

    scalar_changes = diff_scalar_fields(
        previous,
        current,
    )

    market_diff = diff_full_markets(
        previous,
        current,
    )

    added_markets = market_diff[
        "added_markets"
    ]

    removed_markets = market_diff[
        "removed_markets"
    ]

    changed_markets = market_diff[
        "changed_markets"
    ]

    summary = DiffSummary()

    summary.added_markets = len(
        added_markets
    )

    summary.removed_markets = len(
        removed_markets
    )

    print()
    print("=" * 100)
    print(
        f"MERGED FULL-STATE DIFF "
        f"gameId={game_id}"
    )
    print("=" * 100)

    print(f"Source: {source}")

    has_changes = bool(
        scalar_changes
        or added_markets
        or removed_markets
        or changed_markets
    )

    summary.has_changes = has_changes

    if not has_changes:
        print(
            "NO LOGICAL FULL-STATE CHANGE"
        )
        return summary

    if scalar_changes:
        print()
        print("SCALAR FIELD CHANGES")
        print("-" * 100)

        for field, change in (
            scalar_changes.items()
        ):
            print(
                f"{field}: "
                f"{change['old']} "
                f"-> "
                f"{change['new']}"
            )

    if added_markets:
        print()
        print("ADDED MARKETS")
        print("-" * 100)

        for market_id in added_markets:
            print(market_id)

    if removed_markets:
        print()
        print("REMOVED MARKETS")
        print("-" * 100)

        for market_id in removed_markets:
            print(market_id)

    if changed_markets:
        print()
        print("CHANGED MARKETS")
        print("-" * 100)

        for (
            market_id,
            market_change,
        ) in changed_markets.items():

            print()
            print(
                f"market={market_id}"
            )

            added_selections = (
                market_change[
                    "added_selections"
                ]
            )

            removed_selections = (
                market_change[
                    "removed_selections"
                ]
            )

            changed_selections = (
                market_change[
                    "changed_selections"
                ]
            )

            summary.added_selections += len(
                added_selections
            )

            summary.removed_selections += len(
                removed_selections
            )

            if added_selections:
                print(
                    "  added selections:",
                    added_selections,
                )

            if removed_selections:
                print(
                    "  removed selections:",
                    removed_selections,
                )

            for (
                selection_id,
                changes,
            ) in changed_selections.items():

                print(
                    f"  selection="
                    f"{selection_id}"
                )

                for (
                    field,
                    change,
                ) in changes.items():

                    if field == "coef":
                        summary.price_changes += 1

                    if field == "lock":
                        summary.lock_changes += 1

                    print(
                        f"    {field}: "
                        f"{change['old']} "
                        f"-> "
                        f"{change['new']}"
                    )

    return summary


# ======================================================================================
# CONSISTENCY
# ======================================================================================


def state_selection_count_matches_pc(
    game: dict[str, Any],
) -> bool | None:
    pc = game.get("pc")

    if not isinstance(pc, int):
        return None

    return (
        count_selections(game)
        == pc
    )


# ======================================================================================
# MAIN
# ======================================================================================


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s "
            "%(levelname)s "
            "%(name)s - "
            "%(message)s"
        ),
    )

    mqtt_client = MystakeMqttClient()
    cache_client = MystakeCacheClient()

    notification_processor = (
        NotificationProcessor(
            cache_client
        )
    )

    prematch_http_client = httpx.Client(
        timeout=30.0,
        follow_redirects=True,
    )

    previous_notification_payload: str | None = (
        None
    )

    game_states: dict[
        int | str,
        dict[str, Any],
    ] = {}

    team_map: dict[
        int | str,
        str,
    ] = {}

    stats = PrematchStats()

    try:
        mqtt_client.connect()

        mqtt_client.subscribe(
            MQTT_TOPIC
        )

        print("=" * 100)
        print(
            "PREMATCH FULL STATE + "
            "DELTA MERGE SOAK TEST"
        )
        print("=" * 100)

        print(
            f"MQTT topic : {MQTT_TOPIC}"
        )

        print(
            f"API context: "
            f"{PREMATCH_CONTEXT_ID}"
        )

        print(
            f"Language   : "
            f"{PREMATCH_LANGUAGE}"
        )

        print()
        print(
            "Waiting for prematch "
            "notifications..."
        )

        while True:
            message = (
                mqtt_client.receive_publish()
            )

            if message.topic != MQTT_TOPIC:
                continue

            processed = (
                notification_processor.process(
                    message
                )
            )

            data = processed.data

            if not isinstance(data, dict):
                continue

            stats.notifications += 1

            current_payload = (
                canonical_json(data)
            )

            duplicate_payload = (
                previous_notification_payload
                is not None
                and current_payload
                == previous_notification_payload
            )

            print()
            print("=" * 100)
            print(
                f"PREMATCH NOTIFICATION "
                f"#{stats.notifications}"
            )
            print("=" * 100)

            update_list = data.get(
                "UpdateList"
            )

            delete_list = data.get(
                "DeleteList"
            )

            if not isinstance(
                update_list,
                list,
            ):
                update_list = []

            if not isinstance(
                delete_list,
                list,
            ):
                delete_list = []

            print(
                f"UpdateList : "
                f"{len(update_list)}"
            )

            print(
                f"DeleteList : "
                f"{len(delete_list)}"
            )

            print(
                f"Duplicate  : "
                f"{duplicate_payload}"
            )

            if duplicate_payload:
                stats.duplicate_notifications += 1

                print(
                    "Skipping exact duplicate "
                    "notification."
                )

                previous_notification_payload = (
                    current_payload
                )

                continue

            game_ids: list[
                int | str
            ] = []

            print()
            print("UPDATE GAME IDS")
            print("-" * 100)

            for item in update_list:
                if not isinstance(
                    item,
                    dict,
                ):
                    continue

                game_id = item.get(
                    "GameId"
                )

                timestamp = item.get(
                    "UpdateTimeStamp"
                )

                if game_id is None:
                    continue

                known = (
                    game_id
                    in game_states
                )

                print(
                    f"GameId={game_id} "
                    f"timestamp={timestamp} "
                    f"known={known}"
                )

                game_ids.append(
                    game_id
                )

            if delete_list:
                print()
                print("DELETE LIST OBSERVED")
                print("-" * 100)

                for item in delete_list:
                    print(
                        json.dumps(
                            item,
                            ensure_ascii=False,
                            default=str,
                        )
                    )

            if not game_ids:
                previous_notification_payload = (
                    current_payload
                )
                continue

            try:
                deltas, teams = fetch_gameall(
                    prematch_http_client,
                    game_ids,
                )

            except Exception:
                logging.exception(
                    "getprematchgameall failed "
                    "game_ids=%s",
                    game_ids,
                )

                previous_notification_payload = (
                    current_payload
                )

                continue

            stats.delta_batches += 1
            stats.delta_games += len(
                deltas
            )

            update_team_map(
                team_map,
                teams,
            )

            deltas_by_id = {
                delta.get("id"): delta
                for delta in deltas
                if isinstance(delta, dict)
                and delta.get("id") is not None
            }

            for game_id in game_ids:
                delta = deltas_by_id.get(
                    game_id
                )

                if delta is None:
                    continue

                print()
                print("#" * 100)
                print(
                    f"PROCESS GAME "
                    f"gameId={game_id}"
                )
                print("#" * 100)

                print_delta_summary(
                    delta
                )

                previous_state = (
                    game_states.get(
                        game_id
                    )
                )

                # ==============================================================
                # FIRST SEEN
                # ==============================================================

                if previous_state is None:
                    stats.full_bootstraps += 1

                    print()
                    print(
                        "STATE ACTION: "
                        "FIRST SEEN -> "
                        "FULL BOOTSTRAP"
                    )

                    try:
                        full_state = (
                            fetch_gamefull(
                                prematch_http_client,
                                game_id,
                            )
                        )

                    except Exception:
                        logging.exception(
                            "Full bootstrap failed "
                            "gameId=%s",
                            game_id,
                        )
                        continue

                    game_states[
                        game_id
                    ] = deepcopy(
                        full_state
                    )

                    print_game_summary(
                        full_state,
                        team_map,
                    )

                    print()
                    print(
                        "Full bootstrap "
                        "selection consistency: "
                        f"{state_selection_count_matches_pc(full_state)}"
                    )

                    continue

                # ==============================================================
                # DELTA MERGE
                # ==============================================================

                stats.delta_merges += 1

                print()
                print(
                    "STATE ACTION: "
                    "APPLY PARTIAL DELTA"
                )

                merged_state = (
                    merge_game_delta(
                        previous_state,
                        delta,
                    )
                )

                local_count = (
                    count_selections(
                        merged_state
                    )
                )

                server_pc = (
                    merged_state.get(
                        "pc"
                    )
                )

                print()
                print(
                    "Post-merge selections: "
                    f"local={local_count} "
                    f"server_pc={server_pc}"
                )

                consistency = (
                    state_selection_count_matches_pc(
                        merged_state
                    )
                )

                source = "DELTA_MERGE"

                if consistency is True:
                    stats.count_matches += 1

                elif consistency is False:
                    stats.count_mismatches += 1

                    print()
                    print("!" * 100)
                    print(
                        "STATE COUNT MISMATCH"
                    )
                    print("!" * 100)

                    print(
                        "Performing FULL RESYNC..."
                    )

                    try:
                        merged_state = (
                            fetch_gamefull(
                                prematch_http_client,
                                game_id,
                            )
                        )

                        stats.full_resyncs += 1

                        source = (
                            "FULL_RESYNC_AFTER_"
                            "COUNT_MISMATCH"
                        )

                        print(
                            "FULL RESYNC completed."
                        )

                        print(
                            "Resynced selections: "
                            f"{count_selections(merged_state)}"
                        )

                        print(
                            "Resynced pc        : "
                            f"{merged_state.get('pc')}"
                        )

                    except Exception:
                        logging.exception(
                            "Full resync failed "
                            "gameId=%s",
                            game_id,
                        )

                        continue

                diff_summary = (
                    print_full_state_diff(
                        previous_state,
                        merged_state,
                        source=source,
                    )
                )

                if diff_summary.has_changes:
                    stats.logical_changes += 1
                else:
                    stats.no_op_updates += 1

                stats.price_changes += (
                    diff_summary.price_changes
                )

                stats.lock_changes += (
                    diff_summary.lock_changes
                )

                stats.added_markets += (
                    diff_summary.added_markets
                )

                stats.removed_markets += (
                    diff_summary.removed_markets
                )

                stats.added_selections += (
                    diff_summary.added_selections
                )

                stats.removed_selections += (
                    diff_summary.removed_selections
                )

                game_states[
                    game_id
                ] = deepcopy(
                    merged_state
                )

            previous_notification_payload = (
                current_payload
            )

            if (
                stats.notifications
                % PRINT_STATS_EVERY_NOTIFICATIONS
                == 0
            ):
                print_stats(
                    stats
                )

    except KeyboardInterrupt:
        print()
        print(
            "Soak test stopped by user."
        )

    finally:
        print_stats(
            stats
        )

        mqtt_client.close()
        cache_client.close()
        prematch_http_client.close()


if __name__ == "__main__":
    main()