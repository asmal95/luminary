"""OpenAI LLM provider (OpenAI-compatible chat completions)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from luminary.infrastructure.llm.openai_compatible import OpenAICompatibleChatProvider


class OpenAIProvider(OpenAICompatibleChatProvider):
    """OpenAI API provider."""

    API_URL = "https://api.openai.com/v1/chat/completions"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(
            config,
            api_url=self.API_URL,
            api_key_env="OPENAI_API_KEY",
            default_model="gpt-4o-mini",
            require_api_key=True,
        )
