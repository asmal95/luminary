from __future__ import annotations

import requests
from gitlab.exceptions import GitlabError

from luminary.infrastructure.retry import _should_retry_gitlab_error, _should_retry_http_error


def test_should_retry_http_error_with_unknown_status_is_false():
    error = requests.exceptions.HTTPError("Unknown response")
    assert _should_retry_http_error(error) is False


def test_should_retry_gitlab_error_without_status_non_transient_is_false():
    error = GitlabError("Validation failed")
    error.response_code = None
    assert _should_retry_gitlab_error(error) is False


def test_should_retry_gitlab_error_without_status_transient_is_true():
    error = GitlabError("Connection timed out")
    error.response_code = None
    assert _should_retry_gitlab_error(error) is True
