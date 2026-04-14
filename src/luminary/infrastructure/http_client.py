"""Shared HTTP client utilities (requests + retry/backoff).

We keep HTTP logic centralized to avoid divergence across providers.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

import requests

from luminary.domain.config.retry import RetryConfig
from luminary.infrastructure.retry import _should_retry_http_error

logger = logging.getLogger(__name__)

NON_RETRYABLE_REQUEST_EXCEPTIONS = (
    requests.exceptions.InvalidURL,
    requests.exceptions.InvalidSchema,
    requests.exceptions.MissingSchema,
    requests.exceptions.InvalidHeader,
    requests.exceptions.URLRequired,
)


# Re-export RetryConfig for convenience
__all__ = ["RetryConfig", "retry_config_from_dict", "post_json_with_retries"]


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
    """POST JSON with retry on network errors, 429 and 5xx.

    Args:
        url: URL to POST to
        payload: JSON payload
        headers: HTTP headers
        timeout: Request timeout in seconds
        retry: Retry configuration (Pydantic model)

    Returns:
        Response object
    """
    retry_config = retry

    from tenacity import (
        retry as tenacity_retry,
    )
    from tenacity import (
        retry_if_exception,
        stop_after_attempt,
        wait_exponential,
        wait_random,
    )

    def _retry_condition(exception: Exception) -> bool:
        if isinstance(exception, requests.exceptions.RequestException):
            if isinstance(exception, requests.exceptions.HTTPError):
                return _should_retry_http_error(exception)
            if isinstance(exception, NON_RETRYABLE_REQUEST_EXCEPTIONS):
                return False
            return isinstance(
                exception,
                (requests.exceptions.ConnectionError, requests.exceptions.Timeout),
            )
        return False

    attempts = {"count": 0}

    def _make_request() -> requests.Response:
        attempts["count"] += 1
        logger.debug(f"HTTP POST {url}")
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp

    def _before_sleep(retry_state) -> None:
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        status_code = None
        if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
            status_code = exc.response.status_code
        logger.warning(
            "Retrying HTTP request",
            extra={
                "component": "http_client",
                "operation": "post_json",
                "attempt": retry_state.attempt_number,
                "max_attempts": retry_config.max_attempts,
                "status_code": status_code,
            },
        )

    # Настройка wait с jitter
    wait = wait_exponential(
        multiplier=retry_config.initial_delay,
        exp_base=retry_config.backoff_multiplier,
        min=retry_config.initial_delay,
        max=60.0,
    )
    if retry_config.jitter > 0:
        jitter_amount = retry_config.initial_delay * retry_config.jitter
        wait = wait + wait_random(-jitter_amount, jitter_amount)

    # Прямое применение декоратора tenacity
    @tenacity_retry(
        stop=stop_after_attempt(retry_config.max_attempts),
        wait=wait,
        retry=retry_if_exception(_retry_condition),
        reraise=True,
        before_sleep=_before_sleep,
    )
    def _request_with_retry():
        return _make_request()

    start_time = time.monotonic()
    try:
        response = _request_with_retry()
        duration_ms = int((time.monotonic() - start_time) * 1000)
        logger.info(
            "HTTP request completed",
            extra={
                "component": "http_client",
                "operation": "post_json",
                "attempt": attempts["count"],
                "max_attempts": retry_config.max_attempts,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "retry_count": max(0, attempts["count"] - 1),
            },
        )
        return response
    except requests.exceptions.HTTPError:
        raise
    except Exception as e:
        raise RuntimeError(f"HTTP request failed: {e}") from e
