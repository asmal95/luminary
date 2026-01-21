from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
import requests

from luminary.infrastructure.http_client import RetryConfig, post_json_with_retries


def _make_response(status_code: int, payload: dict | None = None) -> requests.Response:
    r = requests.Response()
    r.status_code = status_code
    r.url = "http://example.test"
    if payload is None:
        payload = {}
    r._content = json.dumps(payload).encode("utf-8")  # type: ignore[attr-defined]
    r.headers["Content-Type"] = "application/json"
    return r


def test_post_json_retries_on_5xx(monkeypatch):
    calls = {"n": 0}

    def fake_post(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _make_response(500, {"error": "boom"})
        return _make_response(200, {"ok": True})

    monkeypatch.setattr(requests, "post", fake_post)
    # Patch tenacity's sleep instead of time.sleep
    monkeypatch.setattr("tenacity.nap.sleep", lambda *_: None)

    resp = post_json_with_retries(
        "http://example.test",
        payload={"x": 1},
        headers={"Content-Type": "application/json"},
        timeout=1,
        retry=RetryConfig(max_attempts=2, initial_delay=0, backoff_multiplier=2, jitter=0),
    )
    assert resp.status_code == 200
    assert calls["n"] == 2


def test_post_json_does_not_retry_on_401(monkeypatch):
    def fake_post(*args, **kwargs):
        return _make_response(401, {"error": "unauthorized"})

    monkeypatch.setattr(requests, "post", fake_post)
    # Patch tenacity's sleep instead of time.sleep
    monkeypatch.setattr("tenacity.nap.sleep", lambda *_: None)

    with pytest.raises(requests.HTTPError):
        post_json_with_retries(
            "http://example.test",
            payload={"x": 1},
            headers={"Content-Type": "application/json"},
            timeout=1,
            retry=RetryConfig(max_attempts=3, initial_delay=0, backoff_multiplier=2, jitter=0),
        )


def test_post_json_with_jitter(monkeypatch):
    """Test that jitter adds randomness to retry delays"""
    calls = {"n": 0}
    sleep_calls = []

    def fake_post(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            return _make_response(500, {"error": "boom"})
        return _make_response(200, {"ok": True})

    def mock_sleep(delay):
        sleep_calls.append(delay)

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr("tenacity.nap.sleep", mock_sleep)

    resp = post_json_with_retries(
        "http://example.test",
        payload={"x": 1},
        headers={"Content-Type": "application/json"},
        timeout=1,
        retry=RetryConfig(max_attempts=3, initial_delay=1.0, backoff_multiplier=2, jitter=0.1),
    )
    assert resp.status_code == 200
    assert calls["n"] == 3
    assert len(sleep_calls) == 2
    # With jitter=0.1, delays should vary by +/-10%
    # First delay: 1.0 * 2^0 = 1.0 +/- 0.1
    assert 0.9 <= sleep_calls[0] <= 1.1
    # Second delay: 1.0 * 2^1 = 2.0 +/- 0.2
    assert 1.8 <= sleep_calls[1] <= 2.2


def test_post_json_with_custom_backoff_multiplier(monkeypatch):
    """Test that custom backoff_multiplier works correctly"""
    calls = {"n": 0}
    sleep_calls = []

    def fake_post(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            return _make_response(500, {"error": "boom"})
        return _make_response(200, {"ok": True})

    def mock_sleep(delay):
        sleep_calls.append(delay)

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr("tenacity.nap.sleep", mock_sleep)

    resp = post_json_with_retries(
        "http://example.test",
        payload={"x": 1},
        headers={"Content-Type": "application/json"},
        timeout=1,
        retry=RetryConfig(max_attempts=3, initial_delay=1.0, backoff_multiplier=3, jitter=0),
    )
    assert resp.status_code == 200
    assert len(sleep_calls) == 2
    # With backoff_multiplier=3: first delay = 1.0, second delay = 3.0
    assert sleep_calls[0] == pytest.approx(1.0, rel=0.1)
    assert sleep_calls[1] == pytest.approx(3.0, rel=0.1)

