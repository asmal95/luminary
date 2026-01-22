"""FileChange model - represents changes in a file"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Hunk:
    """Represents a hunk (block of changes) in a file"""

    old_start: int  # Starting line number in old file
    old_count: int  # Number of lines in old file
    new_start: int  # Starting line number in new file
    new_count: int  # Number of lines in new file
    lines: List[str]  # Lines of the hunk (with +/- prefixes)


@dataclass
class FileChange:
    """Represents changes in a single file"""

    path: str  # File path
    old_path: Optional[str] = None  # For renamed files
    status: str = "modified"  # modified, added, deleted, renamed
    hunks: List[Hunk] = None  # List of change hunks
    old_content: Optional[str] = None  # Full content of old file (if available)
    new_content: Optional[str] = None  # Full content of new file (if available)

    def __post_init__(self):
        if self.hunks is None:
            self.hunks = []

    @property
    def is_binary(self) -> bool:
        """Check if file is binary (simple heuristic)"""
        if self.new_content:
            try:
                # Handle both str and bytes
                if isinstance(self.new_content, bytes):
                    self.new_content.decode("utf-8")
                else:
                    self.new_content.encode("utf-8")
                return False
            except (UnicodeEncodeError, UnicodeDecodeError):
                return True
        return False

    @property
    def total_lines_changed(self) -> int:
        """Calculate total number of lines changed"""
        return sum(hunk.old_count + hunk.new_count for hunk in self.hunks if self.hunks)
