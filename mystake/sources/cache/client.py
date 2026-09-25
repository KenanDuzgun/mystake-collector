import logging
import random
import time
from urllib.parse import urlencode

import httpx

from mystake.config import CACHE_GET_BASE_URL

logger = logging.getLogger(__name__)


def build_cache_get_url(key: str) -> str:
    """
    Build a `{CACHE_GET_BASE_URL}?key=<key>` cache-resource URL, per
    the pattern observed for `prematch/games` (docs/handoff/handoff.md
    section 7). Used for cache resources fetched proactively (i.e. not
    received as a `cache:` URL inside an MQTT PUBLISH payload).
    """
    return f"{CACHE_GET_BASE_URL}?{urlencode({'key': key})}"


class MystakeCacheClient:
    def __init__(
        self,
        timeout_seconds: float = 10.0,
        max_attempts: int = 5,
        retry_initial_delay: float = 1.0,
        retry_max_delay: float = 30.0,
    ) -> None:
        self.max_attempts = max_attempts
        self.retry_initial_delay = retry_initial_delay
        self.retry_max_delay = retry_max_delay

        self.client = httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/153.0.0.0 Safari/537.36"
                ),
                "Accept": "*/*",
            },
        )

    def get(self, url: str) -> bytes:
        delay = self.retry_initial_delay

        for attempt in range(
            1,
            self.max_attempts + 1,
        ):
            try:
                response = self.client.get(url)

                if response.status_code in {
                    429,
                    502,
                    503,
                    504,
                }:
                    raise RetryableHttpError(
                        status_code=response.status_code,
                        url=url,
                    )

                response.raise_for_status()

                logger.info(
                    "Cache response received url=%s status=%s bytes=%s",
                    url,
                    response.status_code,
                    len(response.content),
                )

                return response.content

            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                RetryableHttpError,
            ) as exc:
                if attempt >= self.max_attempts:
                    raise

                jitter = random.uniform(
                    0,
                    delay * 0.2,
                )

                sleep_seconds = min(
                    delay + jitter,
                    self.retry_max_delay,
                )

                logger.warning(
                    "Cache request failed attempt=%s/%s retry_in=%.2fs error=%s",
                    attempt,
                    self.max_attempts,
                    sleep_seconds,
                    exc,
                )

                time.sleep(sleep_seconds)

                delay = min(
                    delay * 2,
                    self.retry_max_delay,
                )

        raise RuntimeError("Cache request failed unexpectedly")

    def close(self) -> None:
        self.client.close()


class RetryableHttpError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        url: str,
    ) -> None:
        super().__init__(f"Retryable HTTP status={status_code} url={url}")

        self.status_code = status_code
        self.url = url
