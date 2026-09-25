from mystake.pipeline.prematch_snapshot_hydration import (
    PrematchSnapshotHydrator,
    build_prematch_gamefull_url,
)


class FakeHttpClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.urls: list[str] = []

    def get_json(self, url: str):
        self.urls.append(url)
        response = self._responses.pop(0)

        if isinstance(response, Exception):
            raise response

        return response


GAMEFULL_OUTER = {
    "game": (
        '{"id": 76513163, "t1": 88529, "t2": 17659, "mc": 1, "pc": 2, '
        '"ev": {"448": {"11614425717": {"pos": 1, "coef": 5.36, "lock": false}, '
        '"11614425718": {"pos": 2, "coef": 4.0, "lock": false}}}}'
    ),
    "price": "[]",
    "disableMarkets": None,
}


def test_build_url_uses_context_id_and_game_id():
    url = build_prematch_gamefull_url(76513163)

    assert url == (
        "https://analytics-sp.googleserv.tech/api/prematch/"
        "getprematchgamefull/28/76513163"
    )


def test_fetch_decodes_snapshot_and_preserves_decimal_precision():
    hydrator = PrematchSnapshotHydrator(FakeHttpClient([GAMEFULL_OUTER]))

    snapshot = hydrator.fetch(76513163)

    assert snapshot is not None
    assert snapshot.game_id == 76513163
    assert len(snapshot.markets) == 1

    market = snapshot.markets[0]
    assert market.id == "448"
    assert len(market.selections) == 2

    selection = next(s for s in market.selections if s.id == "11614425717")
    assert selection.price == 5.36
    # Exact decimal odds representation preserved on raw, not the float
    # coercion used for the typed `price` field.
    assert str(selection.raw["coef"]) == "5.36"


def test_fetch_returns_none_on_http_failure():
    hydrator = PrematchSnapshotHydrator(FakeHttpClient([RuntimeError("boom")]))

    assert hydrator.fetch(1) is None


def test_fetch_returns_none_on_malformed_outer_payload():
    hydrator = PrematchSnapshotHydrator(FakeHttpClient([{"unexpected": "shape"}]))

    assert hydrator.fetch(1) is None


def test_fetch_returns_none_on_non_json_game_string():
    hydrator = PrematchSnapshotHydrator(
        FakeHttpClient([{"game": "not json", "price": "[]"}])
    )

    assert hydrator.fetch(1) is None


def test_fetch_returns_none_on_empty_response():
    hydrator = PrematchSnapshotHydrator(FakeHttpClient([None]))

    assert hydrator.fetch(1) is None


def test_fetch_accepts_game_already_decoded_as_dict():
    hydrator = PrematchSnapshotHydrator(
        FakeHttpClient([{"game": {"id": 5, "ev": {}}, "price": "[]"}])
    )

    snapshot = hydrator.fetch(5)

    assert snapshot is not None
    assert snapshot.game_id == 5
    assert snapshot.markets == ()
