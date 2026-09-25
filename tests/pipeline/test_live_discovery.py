import base64
import json
from pathlib import Path

from mystake.config import CACHE_GET_BASE_URL, LIVE_HEADER_CACHE_KEY
from mystake.pipeline.live_discovery import LiveFixtureDiscovery

LIVE_HEADER_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "mystake-live-header-sanitized.json"
)


def wrap_cache_payload(payload: dict) -> bytes:
    raw_json = json.dumps(payload).encode("utf-8")
    encoded = base64.b64encode(raw_json).decode("ascii")
    return json.dumps(encoded).encode("utf-8")


LIVE_HEADER_PAYLOAD = {
    "Games": [
        {"ID": 1, "Sport": "Soccer"},
    ],
    "Sports": [],
    "Regions": [],
    "Championats": [],
    "Teams": [],
    "mk": [],
}


class FakeCacheClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requested_urls = []

    def get(self, url: str) -> bytes:
        self.requested_urls.append(url)

        response = self._responses.pop(0)

        if isinstance(response, Exception):
            raise response

        return response


def test_refresh_discovers_live_fixtures():
    cache_client = FakeCacheClient(
        [wrap_cache_payload(LIVE_HEADER_PAYLOAD)],
    )
    discovery = LiveFixtureDiscovery(cache_client=cache_client)

    diff = discovery.refresh()

    assert diff is not None
    assert len(diff.added) == 1
    assert discovery.registry.get(1) is not None


def test_requests_expected_cache_url():
    cache_client = FakeCacheClient(
        [wrap_cache_payload(LIVE_HEADER_PAYLOAD)],
    )
    discovery = LiveFixtureDiscovery(cache_client=cache_client)

    discovery.refresh()

    assert cache_client.requested_urls == [
        f"{CACHE_GET_BASE_URL}?key={LIVE_HEADER_CACHE_KEY.replace('/', '%2F')}"
    ]


def test_http_failure_preserves_previous_registry():
    cache_client = FakeCacheClient(
        [
            wrap_cache_payload(LIVE_HEADER_PAYLOAD),
            RuntimeError("boom"),
        ],
    )
    discovery = LiveFixtureDiscovery(cache_client=cache_client)

    discovery.refresh()
    previous_fixtures = discovery.registry.list_all()

    diff = discovery.refresh()

    assert diff is None
    assert discovery.registry.list_all() == previous_fixtures


def test_malformed_cache_response_preserves_previous_registry():
    cache_client = FakeCacheClient(
        [
            wrap_cache_payload(LIVE_HEADER_PAYLOAD),
            b"not-valid-outer-json",
        ],
    )
    discovery = LiveFixtureDiscovery(cache_client=cache_client)

    discovery.refresh()
    previous_fixtures = discovery.registry.list_all()

    diff = discovery.refresh()

    assert diff is None
    assert discovery.registry.list_all() == previous_fixtures


def test_refresh_with_captured_fixture_populates_registry_and_resolves_names():
    with LIVE_HEADER_FIXTURE_PATH.open("rb") as handle:
        captured_payload = json.load(handle)

    cache_client = FakeCacheClient([wrap_cache_payload(captured_payload)])
    discovery = LiveFixtureDiscovery(cache_client=cache_client)

    diff = discovery.refresh()

    assert diff is not None
    assert len(diff.added) == 84
    assert len(discovery.registry.list_all()) == 84

    sample = discovery.registry.get(76509222)
    assert sample is not None
    assert sample.sport == "Soccer"
    assert sample.region == "Chile"
    assert sample.champ == "Copa Chile, Knockout stage"
    assert sample.team1 == "CD Everton Vina del Mar"
    assert sample.team2 == "Universidad de Chile"


def test_successful_refresh_with_no_fixture_changes():
    cache_client = FakeCacheClient(
        [
            wrap_cache_payload(LIVE_HEADER_PAYLOAD),
            wrap_cache_payload(LIVE_HEADER_PAYLOAD),
        ],
    )
    discovery = LiveFixtureDiscovery(cache_client=cache_client)

    discovery.refresh()
    diff = discovery.refresh()

    assert diff is not None
    assert diff.has_fixture_changes is False
    assert len(diff.unchanged) == 1
