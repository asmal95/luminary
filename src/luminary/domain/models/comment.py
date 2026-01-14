"""Comment model - represents a code review comment"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Severity(str, Enum):
    """Severity level of a comment"""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class Comment:
    """Represents a code review comment"""

    content: str  # Comment text (markdown supported)
    line_number: Optional[int] = None  # Line number for inline comments
    line_range: Optional[tuple[int, int]] = None  # Line range (start, end) for multi-line
    severity: Severity = Severity.INFO  # Severity level
    file_path: Optional[str] = None  # File path this comment refers to

    def __post_init__(self):
        """Validate comment data"""
        if self.line_number is not None and self.line_number < 1:
            raise ValueError("Line number must be >= 1")
        if self.line_range:
            start, end = self.line_range
            if start < 1 or end < start:
                raise ValueError("Invalid line range")

    @property
    def is_inline(self) -> bool:
        """Check if comment is inline (attached to specific lines)"""
        return self.line_number is not None or self.line_range is not None

    def to_markdown(self) -> str:
        """Format comment as markdown"""
        severity_prefix = f"**[{self.severity.value.upper()}]** " if self.severity != Severity.INFO else ""
        location = ""
        if self.line_number:
            location = f"\n\n**Location:** Line {self.line_number}"
        elif self.line_range:
            start, end = self.line_range
            location = f"\n\n**Location:** Lines {start}-{end}"

        return f"{severity_prefix}{self.content}{location}"
