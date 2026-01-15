"""OpenAI-compatible chat-completions provider base.

This module is used to implement multiple providers (OpenAI, DeepSeek, vLLM)
that expose an OpenAI-compatible /v1/chat/completions API.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from luminary.infrastructure.llm.base import LLMProvider
from luminary.infrastructure.http_client import post_json_with_retries, retry_config_from_dict

logger = logging.getLogger(__name__)


class OpenAICompatibleChatProvider(LLMProvider):
    """OpenAI-compatible provider using chat completions endpoint."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        api_url: str,
        api_key_env: Optional[str],
        default_model: str,
        require_api_key: bool = True,
        extra_headers: Optional[Dict[str, str]] = None,
    ):
        if config is None:
            config = {}
        self._api_url = api_url
        self._api_key_env = api_key_env
        self._require_api_key = require_api_key
        self._extra_headers = extra_headers or {}
        super().__init__(config)

        self.api_key = config.get("api_key") or (os.getenv(api_key_env) if api_key_env else None)
        self.model = config.get("model", default_model)
        self.temperature = config.get("temperature", 0.7)
        self.max_tokens = config.get("max_tokens", 2000)
        self.top_p = config.get("top_p", 0.9)
        self.timeout = float(config.get("timeout", 60))
        self.retry = retry_config_from_dict(config)

    def _validate_config(self, config: Dict[str, Any]) -> None:
        api_key = config.get("api_key") or (os.getenv(self._api_key_env) if self._api_key_env else None)
        if self._require_api_key and not api_key:
            env_name = self._api_key_env or "<unset>"
            raise ValueError(
                "API key is required. "
                f"Set {env_name} environment variable or provide api_key in config."
            )

        if "model" in config and not isinstance(config["model"], str):
            raise ValueError("model must be a string")

        if "temperature" in config:
            temp = config["temperature"]
            if not isinstance(temp, (int, float)) or not (0.0 <= temp <= 2.0):
                raise ValueError("temperature must be between 0.0 and 2.0")

        if "top_p" in config:
            top_p = config["top_p"]
            if not isinstance(top_p, (int, float)) or not (0.0 <= top_p <= 1.0):
                raise ValueError("top_p must be between 0.0 and 1.0")

        if "max_tokens" in config:
            max_tok = config["max_tokens"]
            if not isinstance(max_tok, int) or max_tok < 1:
                raise ValueError("max_tokens must be a positive integer")

    def generate(self, prompt: str, **kwargs) -> str:
        model = kwargs.get("model", self.model)
        temperature = kwargs.get("temperature", self.temperature)
        max_tokens = kwargs.get("max_tokens", self.max_tokens)
        top_p = kwargs.get("top_p", self.top_p)

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": top_p,
        }

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self._extra_headers)

        try:
            response = post_json_with_retries(
                self._api_url, payload=payload, headers=headers, timeout=self.timeout, retry=self.retry
            )
        except Exception as e:
            raise RuntimeError(f"LLM API request failed: {e}") from e

        try:
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            raise RuntimeError(f"Failed to parse LLM response JSON: {e}") from e

