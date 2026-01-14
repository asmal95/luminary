"""OpenRouter LLM provider"""

import logging
import os
import time
from typing import Any, Dict, Optional

import requests

from luminary.infrastructure.llm.base import LLMProvider

logger = logging.getLogger(__name__)


class OpenRouterProvider(LLMProvider):
    """OpenRouter API provider"""

    API_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, config: Dict[str, Any] = None):
        """Initialize OpenRouter provider
        
        Args:
            config: Configuration dictionary with:
                - api_key: OpenRouter API key (or from OPENROUTER_API_KEY env)
                - model: Model name (default: "anthropic/claude-3.5-sonnet")
                - temperature: Temperature (default: 0.7)
                - max_tokens: Max tokens (default: 2000)
                - max_retries: Max retry attempts (default: 3)
                - retry_delay: Initial retry delay in seconds (default: 1)
        """
        if config is None:
            config = {}
        super().__init__(config)

        self.api_key = config.get("api_key") or os.getenv("OPENROUTER_API_KEY")
        self.model = config.get("model", "anthropic/claude-3.5-sonnet")
        self.temperature = config.get("temperature", 0.7)
        self.max_tokens = config.get("max_tokens", 2000)
        self.max_retries = config.get("max_retries", 3)
        self.retry_delay = config.get("retry_delay", 1)

    def _validate_config(self, config: Dict[str, Any]) -> None:
        """Validate OpenRouter configuration"""
        api_key = config.get("api_key") or os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError(
                "OpenRouter API key is required. "
                "Set OPENROUTER_API_KEY environment variable or provide in config."
            )

        if "model" in config and not isinstance(config["model"], str):
            raise ValueError("model must be a string")

        if "temperature" in config:
            temp = config["temperature"]
            if not isinstance(temp, (int, float)) or not (0.0 <= temp <= 2.0):
                raise ValueError("temperature must be between 0.0 and 2.0")

        if "max_tokens" in config:
            max_tok = config["max_tokens"]
            if not isinstance(max_tok, int) or max_tok < 1:
                raise ValueError("max_tokens must be a positive integer")

    def generate(self, prompt: str, **kwargs) -> str:
        """Generate response from OpenRouter API
        
        Args:
            prompt: Input prompt
            **kwargs: Additional parameters (override config)
            
        Returns:
            Generated text response
            
        Raises:
            RuntimeError: If generation fails after retries
        """
        # Override config with kwargs
        model = kwargs.get("model", self.model)
        temperature = kwargs.get("temperature", self.temperature)
        max_tokens = kwargs.get("max_tokens", self.max_tokens)

        messages = [{"role": "user", "content": prompt}]

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # Retry logic with exponential backoff
        last_error = None
        for attempt in range(self.max_retries):
            try:
                logger.debug(
                    f"OpenRouter API call (attempt {attempt + 1}/{self.max_retries})"
                )
                response = requests.post(
                    self.API_URL, json=payload, headers=headers, timeout=60
                )
                response.raise_for_status()

                data = response.json()
                content = data["choices"][0]["message"]["content"]
                logger.debug(f"OpenRouter API response received ({len(content)} chars)")
                return content

            except requests.exceptions.HTTPError as e:
                status_code = e.response.status_code if e.response else None
                last_error = e

                # Don't retry on auth errors
                if status_code in (401, 403):
                    logger.error(f"OpenRouter API authentication error: {e}")
                    raise RuntimeError(f"OpenRouter API authentication failed: {e}") from e

                # Don't retry on client errors (except rate limits)
                if status_code and 400 <= status_code < 500 and status_code != 429:
                    logger.error(f"OpenRouter API client error: {e}")
                    raise RuntimeError(f"OpenRouter API client error: {e}") from e

                # Retry on rate limits and server errors
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2 ** attempt)  # Exponential backoff
                    logger.warning(
                        f"OpenRouter API error (attempt {attempt + 1}/{self.max_retries}): {e}. "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(f"OpenRouter API failed after {self.max_retries} attempts: {e}")
                    raise RuntimeError(
                        f"OpenRouter API request failed after {self.max_retries} attempts: {e}"
                    ) from e

            except requests.exceptions.RequestException as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2 ** attempt)
                    logger.warning(
                        f"OpenRouter API network error (attempt {attempt + 1}/{self.max_retries}): {e}. "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(f"OpenRouter API network error after {self.max_retries} attempts: {e}")
                    raise RuntimeError(
                        f"OpenRouter API network error after {self.max_retries} attempts: {e}"
                    ) from e

        # Should not reach here, but just in case
        raise RuntimeError(f"OpenRouter API request failed: {last_error}") from last_error
