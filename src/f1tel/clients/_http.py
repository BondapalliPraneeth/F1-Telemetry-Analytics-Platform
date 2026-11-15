"""Shared HTTP client: timeouts, retries with backoff and an on-disk response cache."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from f1tel.manifest import atomic_write_bytes
from f1tel.retry import RetryableHTTPError, retry_call

_RETRY_STATUS = {429, 500, 502, 503, 504}


class CachedJSONClient:
    """GET JSON from an API with a timeout, retries and a persistent file cache.

    Responses are cached forever keyed by URL + params; historical F1 data does not
    change, so a cache hit never touches the network. Pass ``cache_dir=None`` to disable.
    """

    def __init__(
        self,
        base_url: str,
        *,
        cache_dir: Path | None = None,
        timeout_s: float = 20.0,
        retries: int = 4,
        base_delay: float = 1.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.cache_dir = cache_dir
        self.retries = retries
        self.base_delay = base_delay
        self._sleep = sleep
        self._client = httpx.Client(timeout=timeout_s, transport=transport)

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def _cache_path(self, url: str, params: dict[str, Any]) -> Path | None:
        if self.cache_dir is None:
            return None
        key = json.dumps([url, sorted(params.items())], default=str)
        return self.cache_dir / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET ``base_url + path`` and return decoded JSON (cached).

        Raises:
            httpx.HTTPStatusError: for non-retryable 4xx responses.
            RetryableHTTPError / httpx.TransportError: once retries are exhausted.
        """
        params = params or {}
        url = f"{self.base_url}/{path.lstrip('/')}"
        cache = self._cache_path(url, params)
        if cache is not None and cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))

        def attempt() -> Any:
            resp = self._client.get(url, params=params)
            if resp.status_code in _RETRY_STATUS:
                raise RetryableHTTPError(f"{resp.status_code} from {url}")
            resp.raise_for_status()
            return resp.json()

        data = retry_call(
            attempt, retries=self.retries, base_delay=self.base_delay, sleep=self._sleep
        )
        if cache is not None:
            atomic_write_bytes(cache, json.dumps(data).encode("utf-8"))
        return data
