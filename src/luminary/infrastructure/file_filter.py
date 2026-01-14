"""File filtering utilities"""

import fnmatch
import logging
from pathlib import Path
from typing import List

from luminary.domain.models.file_change import FileChange

logger = logging.getLogger(__name__)


class FileFilter:
    """Filter for files based on patterns and binary detection"""

    def __init__(
        self,
        ignore_patterns: List[str] = None,
        ignore_binary: bool = True,
    ):
        """Initialize file filter
        
        Args:
            ignore_patterns: List of glob patterns to ignore
            ignore_binary: Whether to ignore binary files
        """
        self.ignore_patterns = ignore_patterns or []
        self.ignore_binary = ignore_binary

    def should_ignore(self, file_change: FileChange) -> tuple[bool, str]:
        """Check if file should be ignored
        
        Args:
            file_change: File change to check
            
        Returns:
            Tuple of (should_ignore, reason)
        """
        file_path = file_change.path

        # Check binary files
        if self.ignore_binary and file_change.is_binary:
            return True, "binary file"

        # Check patterns
        for pattern in self.ignore_patterns:
            if self._match_pattern(file_path, pattern):
                return True, f"matches pattern: {pattern}"

        return False, ""

    def _match_pattern(self, file_path: str, pattern: str) -> bool:
        """Check if file path matches pattern
        
        Args:
            file_path: File path to check
            pattern: Glob pattern
            
        Returns:
            True if matches
        """
        # Convert pattern to match both forward and back slashes
        pattern = pattern.replace("\\", "/")
        file_path_normalized = file_path.replace("\\", "/")

        # Use fnmatch for glob matching
        return fnmatch.fnmatch(file_path_normalized, pattern) or fnmatch.fnmatch(
            Path(file_path_normalized).name, pattern
        )

    def filter_files(self, file_changes: List[FileChange]) -> tuple[List[FileChange], List[tuple[FileChange, str]]]:
        """Filter list of file changes
        
        Args:
            file_changes: List of file changes to filter
            
        Returns:
            Tuple of (filtered_files, ignored_files_with_reasons)
        """
        filtered = []
        ignored = []

        for file_change in file_changes:
            should_ignore, reason = self.should_ignore(file_change)
            if should_ignore:
                ignored.append((file_change, reason))
                logger.debug(f"Ignoring {file_change.path}: {reason}")
            else:
                filtered.append(file_change)

        if ignored:
            logger.info(f"Filtered out {len(ignored)} files, {len(filtered)} files remaining")

        return filtered, ignored
