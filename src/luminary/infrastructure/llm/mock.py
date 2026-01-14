"""Mock LLM provider for testing and prototyping"""

import time
from typing import Any, Dict

from luminary.infrastructure.llm.base import LLMProvider


class MockLLMProvider(LLMProvider):
    """Mock LLM provider that returns predefined responses"""

    def __init__(self, config: Dict[str, Any] = None):
        """Initialize mock provider
        
        Args:
            config: Optional configuration with:
                - delay: Simulated API delay in seconds (default: 0.1)
                - responses: Dict mapping prompts to responses
        """
        if config is None:
            config = {}
        super().__init__(config)
        self.delay = config.get("delay", 0.1)
        self.responses = config.get("responses", {})

    def _validate_config(self, config: Dict[str, Any]) -> None:
        """Validate mock provider configuration"""
        if "delay" in config and not isinstance(config["delay"], (int, float)):
            raise ValueError("delay must be a number")
        if "delay" in config and config["delay"] < 0:
            raise ValueError("delay must be non-negative")

    def generate(self, prompt: str, **kwargs) -> str:
        """Generate mock response
        
        Args:
            prompt: Input prompt (used to lookup predefined response)
            **kwargs: Ignored for mock provider
            
        Returns:
            Mock response text
        """
        # Simulate API delay
        time.sleep(self.delay)

        # Check for predefined response
        if prompt in self.responses:
            return self.responses[prompt]

        # Default mock response based on prompt content
        if "review" in prompt.lower() or "code" in prompt.lower():
            return self._default_review_response()
        return "Mock LLM response"

    def _default_review_response(self) -> str:
        """Generate default mock review response"""
        return """## Code Review Comments

**Line 15:** Consider extracting this logic into a separate function for better readability.

**Line 23:** This variable name could be more descriptive. Consider using `user_count` instead of `uc`.

**Line 45:** Missing error handling here. Consider adding a try-except block.

**Summary:**
The code is generally well-structured, but could benefit from better error handling and more descriptive variable names.
"""
