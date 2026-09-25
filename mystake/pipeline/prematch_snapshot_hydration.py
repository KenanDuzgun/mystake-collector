from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

from mystake.config import PREMATCH_GETPREMATCHGAMEFULL_URL_TEMPLATE
from mystake.models.snapshot import Snapshot, parse_prematch_snapshot
from mystake.sources.http.client import MystakeHttpClient

logger = logging.getLogger(__name__)


def build_prematch_gamefull_url(game_id: int | str) -> str:
    return PREMATCH_GETPREMATCHGAMEFULL_URL_TEMPLATE.format(game_id=game_id)


class PrematchSnapshotHydrator:
    """
    Fetches and decodes an authoritative `getprematchgamefull` snapshot
    for a single GameId.

    Accuracy rule (AGENTS.md section 5): `fetch` returns `None` on any
    HTTP failure, malformed outer payload, or unparsable `game` object,
    logging the reason - it never fabricates an empty/partial snapshot
    to paper over a failure. The caller (the tracked-game registry) is
    responsible for preserving the previously valid snapshot on `None`.
    """

    def __init__(self, http_client: MystakeHttpClient) -> None:
        self.http_client = http_client

    def fetch(self, game_id: int | str) -> Snapshot | None:
        url = build_prematch_gamefull_url(game_id)

        try:
            outer = self.http_client.get_json(url)
        except Exception:
            logger.exception(
                "getprematchgamefull request failed game_id=%s",
                game_id,
            )
            return None

        try:
            game = _decode_game_object(outer)
        except (TypeError, ValueError):
            logger.exception(
                "getprematchgamefull response could not be decoded game_id=%s",
                game_id,
            )
            return None

        snapshot = parse_prematch_snapshot(game)

        if snapshot.game_id is not None and str(snapshot.game_id) != str(game_id):
            logger.warning(
                "getprematchgamefull returned mismatched game_id "
                "requested=%s received=%s",
                game_id,
                snapshot.game_id,
            )

        return snapshot


def _decode_game_object(outer: Any) -> dict[str, Any]:
    """
    Decode the `{"game": "...JSON string...", "price": ..., "disableMarkets": ...}`
    outer envelope (docs/product/SCHEMA.md section 2) down to the `game`
    dict. Raises `TypeError` for an unexpected value type, or `ValueError`
    if the `game` string is not valid JSON - rather than silently
    returning an empty dict for either.
    """
    if not isinstance(outer, dict):
        raise TypeError(
            f"Unexpected getprematchgamefull response type: {type(outer).__name__}"
        )

    game = outer.get("game")

    if isinstance(game, str):
        try:
            # parse_float=Decimal preserves the exact decimal odds
            # representation from the source JSON text on `raw`
            # (AGENTS.md section 5: no float precision loss on raw
            # values); `Selection.price` still coerces this down to
            # `float` (mystake.models._coerce.coerce_float) for
            # continuity with the existing live-snapshot price type.
            game = json.loads(game, parse_float=Decimal)
        except ValueError as exc:
            raise ValueError(
                "getprematchgamefull 'game' field is not valid JSON"
            ) from exc

    if not isinstance(game, dict):
        raise TypeError(
            f"Unexpected getprematchgamefull 'game' type: {type(game).__name__}"
        )

    return game
