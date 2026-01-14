"""Review prompt templates"""

from typing import Optional

from luminary.domain.models.file_change import FileChange


class ReviewPromptBuilder:
    """Builder for code review prompts"""

    DEFAULT_REVIEW_PROMPT = """You are an expert code reviewer. Review the following code changes and provide constructive feedback.

{context}

Please provide:
1. Inline comments for specific lines that need attention (format: **Line X:** comment)
2. A summary of overall code quality and suggestions (format: **Summary:** text)

Guidelines:
- Be constructive and specific
- Focus on code quality, potential bugs, and improvements
- Use severity levels: INFO (suggestions), WARNING (potential issues), ERROR (critical problems)
- Format inline comments as: **Line X:** [SEVERITY] comment text
- Provide actionable feedback

Be concise but thorough."""

    def __init__(self, custom_prompt: Optional[str] = None):
        """Initialize prompt builder
        
        Args:
            custom_prompt: Custom prompt template (uses default if None)
        """
        self.template = custom_prompt or self.DEFAULT_REVIEW_PROMPT

    def build(self, file_change: FileChange) -> str:
        """Build review prompt for file change
        
        Args:
            file_change: File change to review
            
        Returns:
            Formatted prompt string
        """
        context_parts = []

        # File metadata
        context_parts.append(f"File: {file_change.path}")
        if file_change.old_path and file_change.old_path != file_change.path:
            context_parts.append(f"Renamed from: {file_change.old_path}")
        context_parts.append(f"Status: {file_change.status}")

        # File content (if available)
        if file_change.new_content:
            context_parts.append("\n### Current Code:\n")
            context_parts.append("```")
            # Limit content size to avoid token limits
            content = file_change.new_content
            max_lines = 1000
            lines = content.split("\n")
            if len(lines) > max_lines:
                context_parts.append("\n".join(lines[:max_lines]))
                context_parts.append(f"\n... (truncated, showing first {max_lines} lines) ...")
            else:
                context_parts.append(content)
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
