"""DeepSeek LLM provider (OpenAI-compatible chat completions)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from luminary.infrastructure.llm.openai_compatible import OpenAICompatibleChatProvider


class DeepSeekProvider(OpenAICompatibleChatProvider):
    """DeepSeek API provider."""

    API_URL = "https://api.deepseek.com/v1/chat/completions"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(
            config,
            api_url=self.API_URL,
            api_key_env="DEEPSEEK_API_KEY",
            default_model="deepseek-chat",
            require_api_key=True,
        )

