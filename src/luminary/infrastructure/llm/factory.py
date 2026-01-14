"""Factory for creating LLM providers"""

import logging
from typing import Any, Dict

from luminary.infrastructure.llm.base import LLMProvider
from luminary.infrastructure.llm.mock import MockLLMProvider
from luminary.infrastructure.llm.openrouter import OpenRouterProvider

logger = logging.getLogger(__name__)


class LLMProviderFactory:
    """Factory for creating LLM provider instances"""

    PROVIDERS = {
        "mock": MockLLMProvider,
        "openrouter": OpenRouterProvider,
    }

    @classmethod
    def create(cls, provider_type: str, config: Dict[str, Any] = None) -> LLMProvider:
        """Create LLM provider instance
        
        Args:
            provider_type: Type of provider (mock, openrouter, etc.)
            config: Provider configuration
            
        Returns:
            LLMProvider instance
            
        Raises:
            ValueError: If provider type is not supported
        """
        if config is None:
            config = {}

        provider_type_lower = provider_type.lower()

        if provider_type_lower not in cls.PROVIDERS:
            available = ", ".join(cls.PROVIDERS.keys())
            raise ValueError(
                f"Unknown LLM provider: {provider_type}. "
                f"Available providers: {available}"
            )

        provider_class = cls.PROVIDERS[provider_type_lower]
        logger.info(f"Creating {provider_type_lower} provider")
        return provider_class(config)
