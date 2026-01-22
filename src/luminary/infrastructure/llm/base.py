"""Base LLM provider interface"""

from abc import ABC, abstractmethod
from typing import Any, Dict


class LLMProvider(ABC):
    """Abstract base class for LLM providers"""

    def __init__(self, config: Dict[str, Any]):
        """Initialize provider with configuration

        Args:
            config: Provider configuration dictionary

        Raises:
            ValueError: If configuration is invalid
        """
        self.config = config
        self._validate_config(config)

    def _validate_config(self, config: Dict[str, Any]) -> None:
        """Validate provider configuration

        Args:
            config: Configuration dictionary

        Raises:
            ValueError: If configuration is invalid
        """
        # Override in subclasses for specific validation
        pass

    @abstractmethod
    def generate(self, prompt: str, **kwargs) -> str:
        """Generate response from LLM

        Args:
            prompt: Input prompt
            **kwargs: Additional parameters (temperature, max_tokens, etc.)

        Returns:
            Generated text response

        Raises:
            RuntimeError: If generation fails
        """
        pass
