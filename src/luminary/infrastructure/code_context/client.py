"""REST client for Code Context service."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import requests
from tenacity import retry as tenacity_retry
from tenacity import retry_if_exception, stop_after_attempt, wait_exponential

from luminary.infrastructure.retry import _should_retry_http_error

logger = logging.getLogger(__name__)


class CodeContextClient:
    """HTTP client for Code Context REST API."""

    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def search(
        self,
        query: str,
        *,
        repo_name: Optional[str] = None,
        branch: Optional[str] = None,
        repo_path: Optional[str] = None,
        limit: int = 6,
    ) -> List[Dict[str, Any]]:
        """Run semantic code search."""
        payload: Dict[str, Any] = {"query": query, "limit": limit}
        if repo_name:
            payload["repo_name"] = repo_name
        if branch:
            payload["branch"] = branch
        if repo_path:
            payload["repo_path"] = repo_path

        data = self._request_json("POST", "/search", payload=payload)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("results", "hits", "items"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        return []

    def get_symbol_neighbors(self, symbol_id: str, depth: int = 2) -> List[Dict[str, Any]]:
        """Get neighbors for a symbol id."""
        # Most deployments expose this path.
        data = self._request_json(
            "POST",
            "/symbol/neighbors",
            payload={"symbol_id": symbol_id, "depth": depth},
        )
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            value = data.get("neighbors")
            if isinstance(value, list):
                return value
        return []

    def get_file_context(
        self, file_path: str, *, repo_name: Optional[str] = None, branch: Optional[str] = None
    ) -> Optional[str]:
        """Get full file context from indexed repository."""
        payload: Dict[str, Any] = {"file_path": file_path}
        if repo_name:
            payload["repo_name"] = repo_name
        if branch:
            payload["branch"] = branch

        data = self._request_json("POST", "/file_context", payload=payload)
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            for key in ("content", "text", "context"):
                value = data.get(key)
                if isinstance(value, str):
                    return value
        return None

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Run an HTTP request with retry and decode JSON response."""
        url = f"{self.base_url}{path}"

        def _should_retry(exc: Exception) -> bool:
            if isinstance(exc, requests.exceptions.HTTPError):
                return _should_retry_http_error(exc)
            if isinstance(
                exc,
                (requests.exceptions.ConnectionError, requests.exceptions.Timeout),
            ):
                return True
            return False

        @tenacity_retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception(_should_retry),
            reraise=True,
        )
        def _do_request() -> requests.Response:
            response = requests.request(
                method=method,
                url=url,
                json=payload,
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response

        try:
            response = _do_request()
            return response.json()
        except ValueError:
            logger.debug("Code Context returned non-JSON response for %s", url)
            return None
