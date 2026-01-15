"""vLLM provider (OpenAI-compatible chat completions against a local server)."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from luminary.infrastructure.llm.openai_compatible import OpenAICompatibleChatProvider


class VLLMProvider(OpenAICompatibleChatProvider):
    """vLLM provider.

    Assumes an OpenAI-compatible server (often vLLM) running locally.
    """

    DEFAULT_API_URL = "http://localhost:8000/v1/chat/completions"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        if config is None:
            config = {}

        api_url = config.get("api_url") or os.getenv("VLLM_API_URL") or self.DEFAULT_API_URL

        super().__init__(
            config,
            api_url=api_url,
            api_key_env="VLLM_API_KEY",
            default_model="local-model",
            require_api_key=False,
        )

