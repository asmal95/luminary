"""Shared HTTP client utilities (requests + retry/backoff).

We keep HTTP logic centralized to avoid divergence across providers.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

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


def _sleep_with_jitter(base_delay: float, jitter: float) -> None:
    if base_delay <= 0:
        return
    if jitter <= 0:
        time.sleep(base_delay)
        return
    # jitter as a fraction of base_delay
    delta = base_delay * jitter
    time.sleep(max(0.0, base_delay + random.uniform(-delta, delta)))


def post_json_with_retries(
    url: str,
    *,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    timeout: float,
    retry: RetryConfig,
) -> requests.Response:
    """POST JSON with retry on network errors, 429 and 5xx."""
    last_error: Optional[Exception] = None

    for attempt in range(retry.max_attempts):
        try:
            logger.debug(f"HTTP POST {url} (attempt {attempt + 1}/{retry.max_attempts})")
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else None
            last_error = e

            # Don't retry on auth errors
            if status_code in (401, 403):
                raise

            # Don't retry on most 4xx errors (except rate limit)
            if status_code and 400 <= status_code < 500 and status_code != 429:
                raise

            if attempt < retry.max_attempts - 1:
                delay = retry.initial_delay * (retry.backoff_multiplier**attempt)
                logger.warning(f"HTTP error {status_code} on {url}: {e}. Retrying in {delay}s...")
                _sleep_with_jitter(delay, retry.jitter)
                continue

            raise
        except requests.exceptions.RequestException as e:
            last_error = e
            if attempt < retry.max_attempts - 1:
                delay = retry.initial_delay * (retry.backoff_multiplier**attempt)
                logger.warning(f"HTTP network error on {url}: {e}. Retrying in {delay}s...")
                _sleep_with_jitter(delay, retry.jitter)
                continue
            raise

    raise RuntimeError(f"HTTP request failed: {last_error}") from last_error

