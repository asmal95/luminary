"""LLM configuration model."""

from typing import Literal

from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    """Configuration for LLM provider.
    
    Attributes:
        provider: LLM provider name
        model: Model identifier
        temperature: Sampling temperature (0.0-2.0)
        max_tokens: Maximum tokens in response
        top_p: Nucleus sampling parameter (0.0-1.0)
    """

    provider: Literal["mock", "openrouter", "openai", "deepseek", "vllm"] = "mock"
    model: str = "anthropic/claude-3.5-sonnet"
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(2000, gt=0, le=100000)
    top_p: float = Field(0.9, ge=0.0, le=1.0)
