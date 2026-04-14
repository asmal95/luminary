"""Unified retry utilities using tenacity.

This module provides helper functions for retry conditions.
Retry logic is implemented directly in http_client and gitlab/client using tenacity.
"""

from __future__ import annotations

import requests
from gitlab.exceptions import GitlabError


def _looks_transient_message(message: str) -> bool:
    """Heuristic check for transient network/server errors."""
    if not message:
        return False
    lowered = message.lower()
    transient_markers = (
        "timeout",
        "timed out",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
        "connection refused",
        "service unavailable",
        "bad gateway",
        "gateway timeout",
    )
    return any(marker in lowered for marker in transient_markers)


def _should_retry_http_error(exception: requests.exceptions.HTTPError) -> bool:
    """Check if HTTPError should be retried."""
    status_code = exception.response.status_code if exception.response is not None else None
    if status_code is None:
        # Unknown status without a response is not retried by default.
        return False
    # Don't retry on auth errors or most 4xx (except 429)
    if status_code in (401, 403):
        return False
    if status_code and 400 <= status_code < 500 and status_code != 429:
        return False
    # Retry on 429 and 5xx
    return True


def _should_retry_gitlab_error(exception: GitlabError) -> bool:
    """Check if GitlabError should be retried."""
    status_code = (
        getattr(exception, "response_code", None) if hasattr(exception, "response_code") else None
    )
    if status_code is None:
        return _looks_transient_message(str(exception))
    # Don't retry on auth errors or most 4xx (except 429)
    if status_code in (401, 403):
        return False
    if status_code and 400 <= status_code < 500 and status_code != 429:
        return False
    # Retry on 429 and 5xx
    return True
