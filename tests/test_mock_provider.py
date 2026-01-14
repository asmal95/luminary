"""Tests for MockLLMProvider"""

import time

import pytest

from luminary.infrastructure.llm.mock import MockLLMProvider


def test_mock_provider_basic():
    """Test basic mock provider functionality"""
    provider = MockLLMProvider()
    response = provider.generate("test prompt")
    assert isinstance(response, str)
    assert len(response) > 0


def test_mock_provider_custom_response():
    """Test mock provider with custom responses"""
    config = {
        "responses": {
            "test prompt": "Custom response",
        }
    }
    provider = MockLLMProvider(config)
    response = provider.generate("test prompt")
    assert response == "Custom response"


def test_mock_provider_delay():
    """Test mock provider delay simulation"""
    config = {"delay": 0.1}
    provider = MockLLMProvider(config)

    start = time.time()
    provider.generate("test")
    elapsed = time.time() - start

    assert elapsed >= 0.1


def test_mock_provider_invalid_config():
    """Test mock provider with invalid configuration"""
    with pytest.raises(ValueError):
        MockLLMProvider({"delay": -1})


def test_mock_provider_review_response():
    """Test mock provider returns review-like response for code review prompts"""
    provider = MockLLMProvider()
    response = provider.generate("review this code")
    assert "review" in response.lower() or "code" in response.lower()
