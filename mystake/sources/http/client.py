import logging
import random
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class MystakeHttpClient:
    """
    Plain HTTP JSON client for MyStake API endpoints that return JSON
    directly (e.g. `getheader/en`), as opposed to the cache-indirected
    endpoints handled by `mystake.sources.cache.client.MystakeCacheClient`
    (base64/gzip-wrapped payloads reached via MQTT `cache:` URLs).
    """

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
                "Accept": "application/json",
            },
        )

    def get_json(self, url: str) -> Any:
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

                try:
                    data = response.json()
                except ValueError as exc:
                    raise ValueError(f"Response from {url} is not valid JSON") from exc

                logger.info(
                    "HTTP JSON response received url=%s status=%s bytes=%s",
                    url,
                    response.status_code,
                    len(response.content),
                )

                return data

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
                    "HTTP request failed attempt=%s/%s retry_in=%.2fs error=%s",
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

        raise RuntimeError("HTTP request failed unexpectedly")

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
