"""Simple diff parser for MVP"""

import re
from pathlib import Path
from typing import List, Optional

from luminary.domain.models.file_change import FileChange, Hunk


def parse_unified_diff(diff_content: str, file_path: Optional[str] = None) -> FileChange:
    """Parse unified diff format into FileChange
    
    Supports basic unified diff format:
    --- a/file.py
    +++ b/file.py
    @@ -start,count +start,count @@
    -old line
    +new line
    
    Args:
        diff_content: Diff content as string
        file_path: Optional file path (extracted from diff if not provided)
        
    Returns:
        FileChange object
    """
    lines = diff_content.split("\n")
    
    # Extract file paths
    old_path = None
    new_path = None
    hunks: List[Hunk] = []
    current_hunk = None
    hunk_lines: List[str] = []
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # Parse file headers
        if line.startswith("--- "):
            old_path = line[4:].strip()
            # Remove "a/" prefix if present
            if old_path.startswith("a/"):
                old_path = old_path[2:]
        elif line.startswith("+++ "):
            new_path = line[4:].strip()
            # Remove "b/" prefix if present
            if new_path.startswith("b/"):
                new_path = new_path[2:]
        
        # Parse hunk header: @@ -old_start,old_count +new_start,new_count @@
        elif line.startswith("@@ "):
            # Save previous hunk if exists
            if current_hunk:
                current_hunk.lines = hunk_lines
                hunks.append(current_hunk)
            
            # Parse hunk header
            match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if match:
                old_start = int(match.group(1))
                old_count = int(match.group(2)) if match.group(2) else 1
                new_start = int(match.group(3))
                new_count = int(match.group(4)) if match.group(4) else 1
                
                current_hunk = Hunk(
                    old_start=old_start,
                    old_count=old_count,
                    new_start=new_start,
                    new_count=new_count,
                    lines=[],
                )
                hunk_lines = []
        
        # Parse hunk lines
        elif current_hunk and (line.startswith(" ") or line.startswith("-") or line.startswith("+")):
            hunk_lines.append(line)
        
        i += 1
    
    # Save last hunk
    if current_hunk:
        current_hunk.lines = hunk_lines
        hunks.append(current_hunk)
    
    # Determine file path
    path = file_path or new_path or old_path or "unknown"
    
    # Determine status
    status = "modified"
    if old_path and not new_path:
        status = "deleted"
    elif new_path and not old_path:
        status = "added"
    elif old_path != new_path:
        status = "renamed"
    
    return FileChange(
        path=path,
        old_path=old_path if old_path != new_path else None,
        status=status,
        hunks=hunks,
    )


def parse_file_content(file_path: Path) -> FileChange:
    """Parse a regular file (not diff) into FileChange
    
    For MVP, we treat a regular file as a new file with all content.
    
    Args:
        file_path: Path to file
        
    Returns:
        FileChange object
    """
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    
    # Try to read as text
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        # Binary file
        return FileChange(
            path=str(file_path),
            status="modified",
            new_content=None,  # Binary files don't have text content
        )
    
    return FileChange(
        path=str(file_path),
        status="added",  # Treat as new file for review
        new_content=content,
    )
