"""Comments configuration model."""

from typing import Literal

from pydantic import BaseModel


class CommentsConfig(BaseModel):
    """Configuration for comment generation.
    
    Attributes:
        mode: Comment mode (inline, summary, or both)
        severity_levels: Whether to include severity levels (info/warning/error)
        markdown: Whether comments should use Markdown formatting
    """

    mode: Literal["inline", "summary", "both"] = "both"
    severity_levels: bool = True
    markdown: bool = True
