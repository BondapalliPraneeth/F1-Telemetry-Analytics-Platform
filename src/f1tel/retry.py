"""Retry helper with exponential backoff for flaky network calls."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

import httpx
import requests

T = TypeVar("T")
log = logging.getLogger(__name__)

NETWORK_ERRORS: tuple[type[BaseException], ...] = (
    httpx.TransportError,
    requests.RequestException,
    ConnectionError,
    TimeoutError,
)


class RetryableHTTPError(Exception):
    """Raised for HTTP statuses that are worth retrying (429 and 5xx)."""


def retry_call(
    fn: Callable[[], T],
    *,
    retries: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exceptions: tuple[type[BaseException], ...] = (*NETWORK_ERRORS, RetryableHTTPError),
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call ``fn`` and retry on ``exceptions`` with exponential backoff.

    Args:
        fn: zero-argument callable.
        retries: number of retries after the first attempt.
        base_delay: delay before the first retry; doubles each time up to ``max_delay``.
        exceptions: exception types that trigger a retry; others propagate immediately.
        sleep: injectable sleep function (for tests).

    Raises:
        The last exception once all retries are exhausted.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except exceptions as exc:
            if attempt >= retries:
                raise
            delay = min(max_delay, base_delay * 2**attempt)
            log.warning("retry %d/%d in %.1fs after %r", attempt + 1, retries, delay, exc)
            sleep(delay)
            attempt += 1
