"""Comments configuration model."""

from typing import Literal

from pydantic import BaseModel


class CommentsConfig(BaseModel):
    """Configuration for comment generation.

    Attributes:
        mode: Comment mode (inline, summary, or both)
    """

    mode: Literal["inline", "summary", "both"] = "both"
