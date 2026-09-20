import base64
import gzip
import json
from typing import Any


GZIP_MAGIC = b"\x1f\x8b"


def decode_cache_response(content: bytes) -> Any:
    """
    Decode a MyStake cache response.

    Observed formats:

    1. HTTP body is a JSON string containing Base64.
    2. Base64 decoded content may be:
       - plain JSON
       - gzip-compressed JSON

    Returns the parsed JSON value.
    """
    encoded = _parse_outer_json_string(content)

    decoded = _decode_base64(encoded)

    if decoded.startswith(GZIP_MAGIC):
        decoded = _decompress_gzip(decoded)

    return _parse_json(decoded)


def _parse_outer_json_string(content: bytes) -> str:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Cache response is not valid outer JSON"
        ) from exc

    if not isinstance(value, str):
        raise ValueError(
            "Expected cache response outer JSON value "
            "to be a Base64 string"
        )

    return value


def _decode_base64(encoded: str) -> bytes:
    try:
        return base64.b64decode(
            encoded,
            validate=True,
        )
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError(
            "Cache response contains invalid Base64"
        ) from exc


def _decompress_gzip(content: bytes) -> bytes:
    try:
        return gzip.decompress(content)
    except gzip.BadGzipFile as exc:
        raise ValueError(
            "Cache response contains invalid gzip data"
        ) from exc


def _parse_json(content: bytes) -> Any:
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Decoded cache payload is not valid JSON"
        ) from exc