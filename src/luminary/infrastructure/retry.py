"""Unified retry utilities using tenacity.

This module provides helper functions for retry conditions.
Retry logic is implemented directly in http_client and gitlab/client using tenacity.
"""

from __future__ import annotations

import requests
from gitlab.exceptions import GitlabError


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
    status_code = (
        getattr(exception, "response_code", None) if hasattr(exception, "response_code") else None
    )
    # Don't retry on auth errors or most 4xx (except 429)
    if status_code in (401, 403):
        return False
    if status_code and 400 <= status_code < 500 and status_code != 429:
        return False
    # Retry on 429 and 5xx
    return True
