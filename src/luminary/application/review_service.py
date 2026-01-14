"""Review service - orchestrates code review process"""

import logging
from typing import List, Optional

from luminary.domain.models.comment import Comment, Severity
from luminary.domain.models.file_change import FileChange
from luminary.domain.models.review_result import ReviewResult
from luminary.infrastructure.llm.base import LLMProvider

logger = logging.getLogger(__name__)


class ReviewService:
    """Service for reviewing code changes"""

    def __init__(self, llm_provider: LLMProvider):
        """Initialize review service
        
        Args:
            llm_provider: LLM provider instance
        """
        self.llm_provider = llm_provider

    def review_file(self, file_change: FileChange) -> ReviewResult:
        """Review a single file change
        
        Args:
            file_change: File change to review
            
        Returns:
            Review result with comments
        """
        logger.info(f"Reviewing file: {file_change.path}")

        try:
            # Generate prompt for review
            prompt = self._generate_review_prompt(file_change)

            # Get LLM response
            logger.debug(f"Calling LLM for file: {file_change.path}")
            response = self.llm_provider.generate(prompt)

            # Parse response into comments
            comments = self._parse_llm_response(response, file_change.path)

            # Create review result
            result = ReviewResult(
                file_change=file_change,
                comments=comments,
                summary=self._extract_summary(response),
            )

            logger.info(
                f"Review completed for {file_change.path}: "
                f"{len(comments)} comments generated"
            )
            return result

        except Exception as e:
            logger.error(f"Error reviewing file {file_change.path}: {e}", exc_info=True)
            return ReviewResult(
                file_change=file_change,
                error=str(e),
            )

    def _generate_review_prompt(self, file_change: FileChange) -> str:
        """Generate prompt for code review
        
        Args:
            file_change: File change to review
            
        Returns:
            Formatted prompt string
        """
        # Build context
        context_parts = [f"File: {file_change.path}"]
        context_parts.append(f"Status: {file_change.status}")

        # Add file content if available
        if file_change.new_content:
            context_parts.append("\n### Current Code:\n")
            context_parts.append("```")
            context_parts.append(file_change.new_content)
            context_parts.append("```")

        # Add changes (hunks)
        if file_change.hunks:
            context_parts.append("\n### Changes:\n")
            for hunk in file_change.hunks:
                context_parts.append(f"Lines {hunk.new_start}-{hunk.new_start + hunk.new_count - 1}:")
                for line in hunk.lines:
                    context_parts.append(line)

        # Build prompt
        prompt = f"""You are an expert code reviewer. Review the following code changes and provide constructive feedback.

{chr(10).join(context_parts)}

Please provide:
1. Inline comments for specific lines that need attention
2. A summary of overall code quality and suggestions

Format your response with:
- **Line X:** for inline comments
- **Summary:** for overall feedback

Be constructive and specific in your feedback."""

        return prompt

    def _parse_llm_response(self, response: str, file_path: str) -> List[Comment]:
        """Parse LLM response into Comment objects
        
        Args:
            response: LLM response text
            file_path: Path to the file being reviewed
            
        Returns:
            List of Comment objects
        """
        comments = []
        lines = response.split("\n")

        current_comment = None
        current_line = None

        for line in lines:
            line = line.strip()

            # Check for inline comment (format: **Line X:** or Line X:)
            if "**Line" in line or line.startswith("Line"):
                # Extract line number
                try:
                    # Try to find line number
                    parts = line.split(":")
                    if len(parts) >= 2:
                        line_part = parts[0]
                        # Extract number
                        import re

                        numbers = re.findall(r"\d+", line_part)
                        if numbers:
                            current_line = int(numbers[0])
                            comment_text = ":".join(parts[1:]).strip()
                            if comment_text:
                                current_comment = Comment(
                                    content=comment_text,
                                    line_number=current_line,
                                    file_path=file_path,
                                    severity=Severity.INFO,  # Default, can be improved
                                )
                                comments.append(current_comment)
                except (ValueError, IndexError):
                    pass

            # Check for summary section
            elif "**Summary:**" in line or line.startswith("Summary:"):
                # Skip summary line, it will be extracted separately
                continue

            # Continue building current comment
            elif current_comment and line:
                # Append to current comment
                if current_comment.content:
                    current_comment.content += "\n" + line
                else:
                    current_comment.content = line

        # If no inline comments found, create a general comment
        if not comments and response.strip():
            comments.append(
                Comment(
                    content=response,
                    file_path=file_path,
                    severity=Severity.INFO,
                )
            )

        return comments

    def _extract_summary(self, response: str) -> Optional[str]:
        """Extract summary section from LLM response
        
        Args:
            response: LLM response text
            
        Returns:
            Summary text or None
        """
        lines = response.split("\n")
        summary_started = False
        summary_lines = []

        for line in lines:
            if "**Summary:**" in line or line.strip().startswith("Summary:"):
                summary_started = True
                # Remove "Summary:" prefix
                line = line.replace("**Summary:**", "").replace("Summary:", "").strip()
                if line:
                    summary_lines.append(line)
                continue

            if summary_started:
                if line.strip():
                    summary_lines.append(line.strip())
                else:
                    # Empty line might indicate end of summary
                    break

        return "\n".join(summary_lines) if summary_lines else None
