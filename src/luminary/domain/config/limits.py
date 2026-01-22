"""Processing limits configuration model."""

from typing import Optional

from pydantic import BaseModel, Field


class LimitsConfig(BaseModel):
    """Configuration for processing limits.
    
    Attributes:
        max_files: Maximum number of files to process (None = unlimited)
        max_lines: Maximum lines of changes (None = unlimited)
        max_context_tokens: Maximum tokens for context (triggers chunking)
        chunk_overlap_size: Lines overlap between chunks
    """

    max_files: Optional[int] = Field(None, gt=0)
    max_lines: Optional[int] = Field(None, gt=0)
    max_context_tokens: Optional[int] = Field(None, gt=0)
    chunk_overlap_size: int = Field(200, gt=0)
