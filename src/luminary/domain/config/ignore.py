"""Ignore patterns configuration model."""

from typing import List

from pydantic import BaseModel, Field


class IgnoreConfig(BaseModel):
    """Configuration for file filtering.

    Attributes:
        patterns: Glob patterns to ignore
        binary_files: Whether to ignore binary files
    """

    patterns: List[str] = Field(
        default_factory=lambda: [
            "*.lock",
            "*.min.js",
            "*.min.css",
            "*.map",
            "node_modules/**",
            ".git/**",
        ]
    )
    binary_files: bool = True
