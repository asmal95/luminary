"""Review prompt templates"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from luminary.domain.models.file_change import FileChange


@dataclass(frozen=True)
class ReviewPromptOptions:
    """Options that affect prompt content and expected output."""

    comment_mode: str = "both"  # inline, summary, both
    language: Optional[str] = None
    framework: Optional[str] = None
    line_number_offset: int = 0  # used for chunking; absolute_line = offset + local_line


class ReviewPromptBuilder:
    """Builder for code review prompts"""

    DEFAULT_REVIEW_PROMPT = """You are an expert code reviewer. Review the following code changes and provide constructive feedback.

{context}

CRITICAL REQUIREMENTS:
1. Return ONLY valid JSON. No markdown code blocks, no explanations, no text outside JSON.
2. Use line numbers EXACTLY as shown in the code block above (format: "42: code here").
3. "line" field MUST be a number (integer), NEVER empty, NEVER null.

Required JSON format:

[
  {{
    "file": "src/main/java/Example.java",
    "line": 42,
    "message": "This hardcoded password should be moved to environment variables.",
    "suggestion": null
  }},
  {{
    "file": "src/main/java/Example.java",
    "line": 15,
    "message": "Consider extracting this logic into a separate method.",
    "suggestion": "private void processData() {{\n    // code here\n}}"
  }}
]

Field requirements:
- "file": STRING in double quotes - exact file path
- "line": INTEGER (NOT string!) - MUST match line number from code block (e.g., if code shows "42: code", use 42)
- "message": STRING in double quotes - review comment
- "suggestion": STRING in double quotes OR null (no quotes) - replacement code or null

VALID (correct):
{{"file": "test.py", "line": 5, "message": "Fix this", "suggestion": null}}
{{"file": "test.py", "line": 10, "message": "Improve", "suggestion": "new code"}}

INVALID (never do this):
{{"file": "test.py", "line": , "message": "Fix"}}  ← MISSING LINE NUMBER!
{{"file": "test.py", "line": "5", "message": "Fix"}}  ← LINE MUST BE NUMBER, NOT STRING!
{{"file": "test.py", "line": null, "message": "Fix"}}  ← LINE CANNOT BE NULL!

If summary requested: {{"comments": [...], "summary": "text"}}
If no issues: []

Return ONLY JSON. Nothing else."""

    def __init__(self, custom_prompt: Optional[str] = None):
        """Initialize prompt builder
        
        Args:
            custom_prompt: Custom prompt template (uses default if None)
        """
        self.template = custom_prompt or self.DEFAULT_REVIEW_PROMPT

    def build(self, file_change: FileChange, options: Optional[ReviewPromptOptions] = None) -> str:
        """Build review prompt for file change
        
        Args:
            file_change: File change to review
            
        Returns:
            Formatted prompt string
        """
        options = options or ReviewPromptOptions()
        context_parts = []

        # File metadata
        context_parts.append(f"File: {file_change.path}")
        if file_change.old_path and file_change.old_path != file_change.path:
            context_parts.append(f"Renamed from: {file_change.old_path}")
        context_parts.append(f"Status: {file_change.status}")
        if options.language:
            context_parts.append(f"Language: {options.language}")
        if options.framework:
            context_parts.append(f"Framework: {options.framework}")

        # Comment mode
        if options.comment_mode == "inline":
            context_parts.append("Requested output: inline comments only (JSON array, no summary field).")
        elif options.comment_mode == "summary":
            context_parts.append("Requested output: summary only (return empty comments array [] and include summary field).")
        else:
            context_parts.append("Requested output: inline comments (JSON array) and a summary (optional summary field in JSON).")

        # File content (if available)
        if file_change.new_content:
            context_parts.append("\n### Current Code (with line numbers):\n")
            context_parts.append("```")
            # Limit content size to avoid token limits
            content = file_change.new_content
            max_lines = 1000
            lines = content.split("\n")
            if len(lines) > max_lines:
                numbered = [
                    f"{options.line_number_offset + i + 1}: {line}"
                    for i, line in enumerate(lines[:max_lines])
                ]
                context_parts.append("\n".join(numbered))
                context_parts.append(f"\n... (truncated, showing first {max_lines} lines) ...")
            else:
                numbered = [
                    f"{options.line_number_offset + i + 1}: {line}"
                    for i, line in enumerate(lines)
                ]
                context_parts.append("\n".join(numbered))
            context_parts.append("```")

        # Changes (hunks)
        if file_change.hunks:
            context_parts.append("\n### Changes:\n")
            for i, hunk in enumerate(file_change.hunks, 1):
                context_parts.append(
                    f"\n--- Hunk {i} (Lines {hunk.new_start}-{hunk.new_start + hunk.new_count - 1}) ---"
                )
                for line in hunk.lines:
                    context_parts.append(line)

        context = "\n".join(context_parts)

        # Format prompt with context
        prompt = self.template.format(context=context)

        return prompt
