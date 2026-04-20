"""HTTP retry helper for LLM calls.

Wraps `requests`-based calls (both streaming and non-streaming) with:
- Exponential backoff (base * 2^n) with half-jitter
- Retry-After header honoring for HTTP 429
- Retries on: ConnectionError, Timeout, ReadTimeout,
  HTTPError with status in {408, 425, 429, 500, 502, 503, 504},
  ChunkedEncodingError, urllib3.ProtocolError,
  IncompleteRead, JSONDecodeError (stream corruption),
  IncompleteStreamError (stream ended before completion marker),
  and the "empty response" RuntimeError raised downstream.

Configurable via environment variables:
- LLM_RETRY_MAX_ATTEMPTS (default 5)
- LLM_RETRY_BASE_SECONDS (default 1.0)
- LLM_RETRY_MAX_SECONDS (default 60.0)
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from functools import wraps
from typing import Any, Callable, TypeVar

from requests.exceptions import (
    ChunkedEncodingError,
    ConnectionError as RequestsConnectionError,
    HTTPError,
    ReadTimeout,
    Timeout,
)

try:
    from urllib3.exceptions import IncompleteRead, ProtocolError
except ImportError:  # pragma: no cover
    class ProtocolError(Exception):  # type: ignore
        pass

    class IncompleteRead(Exception):  # type: ignore
        pass


log = logging.getLogger("proof_agent.retry")

RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


class IncompleteStreamError(RuntimeError):
    """Raised when a streamed response ends before its completion marker."""

    def __init__(self, message: str, partial_text: str = ""):
        super().__init__(message)
        self.partial_text = str(partial_text or "")

_NETWORK_EXCEPTIONS: tuple[type[BaseException], ...] = (
    RequestsConnectionError,
    Timeout,
    ReadTimeout,
    ChunkedEncodingError,
    ProtocolError,
    IncompleteRead,
    ConnectionResetError,
    ConnectionAbortedError,
)


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, str(default))).strip())
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, str(default))).strip())
    except (TypeError, ValueError):
        return default


def _max_attempts() -> int:
    return max(1, _env_int("LLM_RETRY_MAX_ATTEMPTS", 5))


def _base_seconds() -> float:
    return max(0.1, _env_float("LLM_RETRY_BASE_SECONDS", 1.0))


def _max_seconds() -> float:
    return max(1.0, _env_float("LLM_RETRY_MAX_SECONDS", 60.0))


def _is_empty_response_error(exc: BaseException) -> bool:
    if not isinstance(exc, RuntimeError):
        return False
    text = str(exc).lower()
    return "empty" in text and ("response" in text or "streaming" in text)


def _classify(exc: BaseException) -> tuple[bool, float | None]:
    """Return (retryable, server_requested_delay_seconds)."""
    if isinstance(exc, IncompleteStreamError):
        return True, None
    if isinstance(exc, _NETWORK_EXCEPTIONS):
        return True, None
    if isinstance(exc, HTTPError):
        resp = getattr(exc, "response", None)
        status = getattr(resp, "status_code", None)
        if status in RETRYABLE_STATUSES:
            delay: float | None = None
            headers = getattr(resp, "headers", None)
            if headers is not None:
                raw = headers.get("Retry-After")
                if raw:
                    try:
                        delay = float(raw)
                    except (TypeError, ValueError):
                        delay = None
            return True, delay
    if isinstance(exc, json.JSONDecodeError):
        return True, None
    if _is_empty_response_error(exc):
        return True, None
    return False, None


def _sleep_duration(attempt: int, server_delay: float | None) -> float:
    cap = _max_seconds()
    if server_delay is not None and server_delay > 0:
        return min(server_delay, cap)
    base = _base_seconds() * (2 ** (attempt - 1))
    base = min(base, cap)
    return base * (0.5 + random.random() * 0.5)


T = TypeVar("T")


def with_http_retry(label: str = "") -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator: retries the wrapped callable on transient network errors."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        tag = label or func.__name__

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            attempts = _max_attempts()
            last_exc: BaseException | None = None
            for attempt in range(1, attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    retryable, server_delay = _classify(exc)
                    if not retryable or attempt >= attempts:
                        if retryable:
                            log.error(
                                "[%s] giving up after %d attempts: %s: %s",
                                tag,
                                attempt,
                                type(exc).__name__,
                                str(exc)[:300],
                            )
                        raise
                    delay = _sleep_duration(attempt, server_delay)
                    log.warning(
                        "[%s] retryable %s on attempt %d/%d: %s — sleeping %.2fs",
                        tag,
                        type(exc).__name__,
                        attempt,
                        attempts,
                        str(exc)[:200],
                        delay,
                    )
                    time.sleep(delay)
                    last_exc = exc
            if last_exc is not None:
                raise last_exc
            raise RuntimeError(f"[{tag}] retry loop exited without result")

        return wrapper

    return decorator
