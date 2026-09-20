import base64
import gzip
import json

import pytest

from mystake.pipeline.cache_decoder import (
    decode_cache_response,
)


def wrap_cache_payload(payload: bytes) -> bytes:
    encoded = base64.b64encode(
        payload
    ).decode("ascii")

    return json.dumps(
        encoded
    ).encode("utf-8")


def test_decode_plain_json_cache_response() -> None:
    payload = {
        "UpdateList": [
            {
                "GameId": 76452176,
                "UpdateTimeStamp": 1789929460804,
            }
        ],
        "DeleteList": [],
    }

    raw_json = json.dumps(
        payload
    ).encode("utf-8")

    content = wrap_cache_payload(
        raw_json
    )

    result = decode_cache_response(
        content
    )

    assert result == payload


def test_decode_gzip_json_cache_response() -> None:
    payload = {
        "Match": {
            "GameID": 74899312,
            "Score": "1:0",
        },
        "gmk": [],
        "mk": [],
        "TimeLines": [],
    }

    raw_json = json.dumps(
        payload
    ).encode("utf-8")

    compressed = gzip.compress(
        raw_json
    )

    content = wrap_cache_payload(
        compressed
    )

    result = decode_cache_response(
        content
    )

    assert result == payload


def test_reject_non_string_outer_json() -> None:
    content = json.dumps(
        {
            "unexpected": "object",
        }
    ).encode("utf-8")

    with pytest.raises(
        ValueError,
        match="outer JSON value",
    ):
        decode_cache_response(
            content
        )


def test_reject_invalid_base64() -> None:
    content = json.dumps(
        "this-is-not-base64!!!"
    ).encode("utf-8")

    with pytest.raises(
        ValueError,
        match="invalid Base64",
    ):
        decode_cache_response(
            content
        )