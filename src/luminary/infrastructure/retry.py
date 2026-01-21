"""Unified retry utilities using tenacity.

This module provides retry decorators and utilities that unify retry logic
across HTTP clients and GitLab API calls.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import requests
from gitlab.exceptions import GitlabError
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    wait_add,
    wait_random,
    before_sleep_log,
)

from luminary.infrastructure.http_client import RetryConfig

logger = logging.getLogger(__name__)


def _should_retry_http_error(exception: requests.exceptions.HTTPError) -> bool:
    """Check if HTTPError should be retried."""
    status_code = exception.response.status_code if exception.response else None
    # Don't retry on auth errors or most 4xx (except 429)
    if status_code in (401, 403):
        return False
    if status_code and 400 <= status_code < 500 and status_code != 429:
        return False
    # Retry on 429 and 5xx
    return True


def _should_retry_gitlab_error(exception: GitlabError) -> bool:
    """Check if GitlabError should be retried."""
    status_code = getattr(exception, "response_code", None) if hasattr(exception, "response_code") else None
    # Don't retry on auth errors or most 4xx (except 429)
    if status_code in (401, 403):
        return False
    if status_code and 400 <= status_code < 500 and status_code != 429:
        return False
    # Retry on 429 and 5xx
    return True


def create_retry_decorator(
    retry_config: RetryConfig,
    retry_condition: Callable[[Exception], bool],
    before_sleep: Callable[[RetryCallState], None] | None = None,
) -> Callable[[Callable], Callable]:
    """Create a retry decorator with tenacity.
    
    Args:
        retry_config: Retry configuration
        retry_condition: Function that returns True if exception should be retried
        before_sleep: Optional callback before sleep (defaults to logging)
    
    Returns:
        Retry decorator
    """
    # Exponential backoff: initial_delay * (backoff_multiplier ^ attempt)
    wait = wait_exponential(
        multiplier=retry_config.initial_delay,
        exp_base=retry_config.backoff_multiplier,
        min=retry_config.initial_delay,
        max=60.0,
    )
    
    # Add jitter if configured
    if retry_config.jitter > 0:
        jitter_amount = retry_config.initial_delay * retry_config.jitter
        wait = wait_add(wait, wait_random(-jitter_amount, jitter_amount))
    
    if before_sleep is None:
        before_sleep = before_sleep_log(logger, logging.WARNING)
    
    def decorator(func: Callable) -> Callable:
        return retry(
            stop=stop_after_attempt(retry_config.max_attempts),
            wait=wait,
            retry=retry_if_exception(retry_condition),
            reraise=True,
            before_sleep=before_sleep,
        )(func)
    
    return decorator


def retry_gitlab_call(retry_config: RetryConfig) -> Callable[[Callable], Callable]:
    """Create a retry decorator for GitLab API calls."""
    
    def _retry_condition(exception: Exception) -> bool:
        if isinstance(exception, GitlabError):
            return _should_retry_gitlab_error(exception)
        return True  # Retry other exceptions
    
    def _before_sleep_log(retry_state: RetryCallState) -> None:
        if retry_state.outcome is None:
            return
        exception = retry_state.outcome.exception()
        attempt = retry_state.attempt_number
        logger.warning(f"GitLab API error (attempt {attempt}/{retry_config.max_attempts}): {exception}. Retrying...")
    
    decorator = create_retry_decorator(retry_config, _retry_condition, _before_sleep_log)
    
    def wrapper(func: Callable) -> Callable:
        retried_func = decorator(func)
        
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            try:
                return retried_func(*args, **kwargs)
            except GitlabError as e:
                status_code = getattr(e, "response_code", None) if hasattr(e, "response_code") else None
                if status_code in (401, 403):
                    logger.error(f"GitLab API authentication error: {e}")
                    raise RuntimeError(f"GitLab API authentication failed: {e}") from e
                elif status_code and 400 <= status_code < 500 and status_code != 429:
                    if status_code == 404:
                        logger.debug(f"GitLab API 404 (file not found): {e}")
                    else:
                        logger.error(f"GitLab API client error: {e}")
                    raise RuntimeError(f"GitLab API client error: {e}") from e
                else:
                    logger.error(f"GitLab API failed after {retry_config.max_attempts} attempts: {e}")
                    raise RuntimeError(f"GitLab API request failed after {retry_config.max_attempts} attempts: {e}") from e
            except Exception as e:
                logger.error(f"GitLab API error after {retry_config.max_attempts} attempts: {e}")
                raise RuntimeError(f"GitLab API error after {retry_config.max_attempts} attempts: {e}") from e
        
        return wrapped
    
    return wrapper
