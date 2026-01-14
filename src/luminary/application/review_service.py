"""Review service - orchestrates code review process"""

import logging
import re
from typing import List, Optional

from luminary.domain.models.comment import Comment, Severity
from luminary.domain.models.file_change import FileChange
from luminary.domain.models.review_result import ReviewResult
from luminary.domain.prompts.review_prompts import ReviewPromptBuilder
from luminary.domain.validators.comment_validator import CommentValidator
from luminary.infrastructure.llm.base import LLMProvider

logger = logging.getLogger(__name__)


class ReviewService:
    """Service for reviewing code changes"""

    def __init__(
        self,
        llm_provider: LLMProvider,
        validator: Optional[CommentValidator] = None,
        custom_review_prompt: Optional[str] = None,
    ):
        """Initialize review service
        
        Args:
            llm_provider: LLM provider instance
            validator: Optional comment validator (if None, comments are not validated)
            custom_review_prompt: Custom review prompt template
        """
        self.llm_provider = llm_provider
        self.validator = validator
        self.prompt_builder = ReviewPromptBuilder(custom_review_prompt)

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
            prompt = self.prompt_builder.build(file_change)

            # Get LLM response
            logger.debug(f"Calling LLM for file: {file_change.path}")
            response = self.llm_provider.generate(prompt)

            # Parse response into comments
            comments = self._parse_llm_response(response, file_change.path)

            # Validate comments if validator is provided
            if self.validator:
                logger.debug(f"Validating {len(comments)} comments")
                validated_comments = []
                for comment in comments:
                    # Extract code snippet for validation context
                    code_snippet = self._extract_code_snippet(file_change, comment)
                    validation_result = self.validator.validate(
                        comment, file_change, code_snippet
                    )
                    if validation_result.valid:
                        validated_comments.append(comment)
                    else:
                        logger.debug(
                            f"Comment rejected: {validation_result.reason} "
                            f"(scores: {validation_result.scores})"
                        )
                comments = validated_comments
                logger.info(
                    f"Validation complete: {len(validated_comments)}/{len(comments)} comments passed"
                )

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

    def _extract_code_snippet(
        self, file_change: FileChange, comment: Comment
    ) -> Optional[str]:
        """Extract relevant code snippet for comment validation
        
        Args:
            file_change: File change
            comment: Comment to extract snippet for
            
        Returns:
            Code snippet or None
        """
        if not file_change.new_content or not comment.line_number:
            return None

        lines = file_change.new_content.split("\n")
        line_idx = comment.line_number - 1

        if 0 <= line_idx < len(lines):
            # Extract 5 lines before and after
            start = max(0, line_idx - 5)
            end = min(len(lines), line_idx + 6)
            snippet_lines = lines[start:end]
            return "\n".join(snippet_lines)

        return None

    def _parse_llm_response(self, response: str, file_path: str) -> List[Comment]:
        """Parse LLM response into Comment objects with improved parsing
        
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
        current_severity = Severity.INFO

        for line in lines:
            original_line = line
            line = line.strip()

            # Check for inline comment with severity
            # Format: **Line X:** [SEVERITY] comment or **Line X:** comment
            line_match = re.search(r"\*\*Line\s+(\d+):\*\*", line, re.IGNORECASE)
            if not line_match:
                line_match = re.search(r"Line\s+(\d+):", line, re.IGNORECASE)

            if line_match:
                current_line = int(line_match.group(1))

                # Extract severity if present
                severity_match = re.search(
                    r"\[(INFO|WARNING|ERROR)\]", line, re.IGNORECASE
                )
                if severity_match:
                    severity_str = severity_match.group(1).upper()
                    current_severity = Severity[severity_str]
                else:
                    current_severity = Severity.INFO

                # Extract comment text (after Line X: and optional [SEVERITY])
                comment_text = line
                # Remove "**Line X:**" or "Line X:"
                comment_text = re.sub(r"\*\*Line\s+\d+:\*\*", "", comment_text, flags=re.IGNORECASE)
                comment_text = re.sub(r"Line\s+\d+:", "", comment_text, flags=re.IGNORECASE)
                # Remove [SEVERITY]
                comment_text = re.sub(r"\[(INFO|WARNING|ERROR)\]", "", comment_text, flags=re.IGNORECASE)
                comment_text = comment_text.strip()

                if comment_text:
                    current_comment = Comment(
                        content=comment_text,
                        line_number=current_line,
                        file_path=file_path,
                        severity=current_severity,
                    )
                    comments.append(current_comment)
                continue

            # Check for summary section
            if "**Summary:**" in line or line.strip().startswith("Summary:"):
                continue

            # Continue building current comment
            if current_comment and line:
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
