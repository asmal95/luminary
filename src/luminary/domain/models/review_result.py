"""ReviewResult model - represents the result of reviewing a file"""

from dataclasses import dataclass, field
from typing import List, Optional

from luminary.domain.models.comment import Comment
from luminary.domain.models.file_change import FileChange


@dataclass
class ReviewResult:
    """Result of reviewing a file"""

    file_change: FileChange
    comments: List[Comment] = field(default_factory=list)
    summary: Optional[str] = None  # Overall summary of the review
    error: Optional[str] = None  # Error message if review failed

    @property
    def is_successful(self) -> bool:
        """Check if review was successful"""
        return self.error is None

    @property
    def inline_comments(self) -> List[Comment]:
        """Get only inline comments"""
        return [c for c in self.comments if c.is_inline]

    @property
    def has_comments(self) -> bool:
        """Check if review has any comments"""
        return len(self.comments) > 0 or self.summary is not None
