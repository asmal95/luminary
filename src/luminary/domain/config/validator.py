"""Validator configuration model."""

from typing import Optional

from pydantic import BaseModel, Field


class ValidatorConfig(BaseModel):
    """Configuration for comment validation.

    Attributes:
        enabled: Whether validation is enabled
        provider: LLM provider (None = use same as main LLM)
        model: Model identifier (None = use same as main LLM)
        threshold: Validation score threshold (0.0-1.0)
    """

    enabled: bool = False
    provider: Optional[str] = None
    model: Optional[str] = None
    threshold: float = Field(0.7, ge=0.0, le=1.0)
