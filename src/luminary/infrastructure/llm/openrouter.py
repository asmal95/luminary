"""OpenRouter LLM provider (OpenAI-compatible chat completions)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from luminary.infrastructure.llm.openai_compatible import OpenAICompatibleChatProvider


class OpenRouterProvider(OpenAICompatibleChatProvider):
    """OpenRouter API provider.

    OpenRouter is close to OpenAI's chat-completions API, so we reuse the common
    implementation. Optional OpenRouter-specific headers can be provided:
    - referer: sent as HTTP-Referer
    - title: sent as X-Title
    """

    API_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        if config is None:
            config = {}

        extra_headers: Dict[str, str] = {}
        referer = config.get("referer")
        title = config.get("title")
        if isinstance(referer, str) and referer:
            extra_headers["HTTP-Referer"] = referer
        if isinstance(title, str) and title:
            extra_headers["X-Title"] = title

        super().__init__(
            config,
            api_url=self.API_URL,
            api_key_env="OPENROUTER_API_KEY",
            default_model="anthropic/claude-3.5-sonnet",
            require_api_key=True,
            extra_headers=extra_headers,
        )
