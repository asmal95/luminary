"""Prompts configuration model."""

from typing import Optional

from pydantic import BaseModel


class PromptsConfig(BaseModel):
    """Configuration for custom prompts.
    
    Attributes:
        review: Custom review prompt template (None = use default)
        validation: Custom validation prompt template (None = use default)
    """

    review: Optional[str] = None
    validation: Optional[str] = None
