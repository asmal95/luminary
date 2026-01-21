"""Shared HTTP client utilities (requests + retry/backoff).

We keep HTTP logic centralized to avoid divergence across providers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

from luminary.infrastructure.retry import call_with_retry, _should_retry_http_error

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int = 3
    initial_delay: float = 1.0
    backoff_multiplier: float = 2.0
    jitter: float = 0.1  # +/-10% by default


def retry_config_from_dict(config: Dict[str, Any]) -> RetryConfig:
    """Parse retry config from dict, supporting legacy aliases."""
    # Preferred keys (ADR-0007)
    max_attempts = config.get("max_attempts")
    initial_delay = config.get("initial_delay")
    backoff_multiplier = config.get("backoff_multiplier")

    # Legacy/provider-specific aliases used in earlier stages
    if max_attempts is None:
        max_attempts = config.get("max_retries", 3)
    if initial_delay is None:
        initial_delay = config.get("retry_delay", 1.0)
    if backoff_multiplier is None:
        backoff_multiplier = config.get("backoff", 2.0)

    # Optional
    jitter = config.get("jitter", 0.1)

    try:
        max_attempts_i = int(max_attempts)
    except Exception:
        max_attempts_i = 3

    try:
        initial_delay_f = float(initial_delay)
    except Exception:
        initial_delay_f = 1.0

    try:
        backoff_multiplier_f = float(backoff_multiplier)
    except Exception:
        backoff_multiplier_f = 2.0

    try:
        jitter_f = float(jitter)
    except Exception:
        jitter_f = 0.1

    if max_attempts_i < 1:
        max_attempts_i = 1
    if initial_delay_f < 0:
        initial_delay_f = 0.0
    if backoff_multiplier_f < 1:
        backoff_multiplier_f = 1.0
    if jitter_f < 0:
        jitter_f = 0.0

    return RetryConfig(
        max_attempts=max_attempts_i,
        initial_delay=initial_delay_f,
        backoff_multiplier=backoff_multiplier_f,
        jitter=jitter_f,
    )


def post_json_with_retries(
    url: str,
    *,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    timeout: float,
    retry: RetryConfig,
) -> requests.Response:
    """POST JSON with retry on network errors, 429 and 5xx."""
    from tenacity import RetryCallState
    from luminary.infrastructure.retry import create_retry_decorator

    def _retry_condition(exception: Exception) -> bool:
        if isinstance(exception, requests.exceptions.RequestException):
            if isinstance(exception, requests.exceptions.HTTPError):
                return _should_retry_http_error(exception)
            return True  # Retry network errors
        return False

    def _before_sleep_log(retry_state: RetryCallState) -> None:
        if retry_state.outcome is None:
            return
        exception = retry_state.outcome.exception()
        if isinstance(exception, requests.exceptions.HTTPError):
            status_code = exception.response.status_code if exception.response else None
            logger.warning(f"HTTP error {status_code} on {url}: {exception}. Retrying...")
        else:
            logger.warning(f"HTTP network error on {url}: {exception}. Retrying...")

    def _make_request() -> requests.Response:
        logger.debug(f"HTTP POST {url}")
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp

    try:
        retry_decorator = create_retry_decorator(retry, _retry_condition, _before_sleep_log)
        return retry_decorator(_make_request)()
    except requests.exceptions.HTTPError:
        raise
    except Exception as e:
        raise RuntimeError(f"HTTP request failed: {e}") from e

