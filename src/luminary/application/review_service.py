"""Review service - orchestrates code review process"""

from __future__ import annotations

import logging
import re
from typing import Iterable, List, Optional, Tuple

from luminary.domain.models.comment import Comment, Severity
from luminary.domain.models.file_change import FileChange
from luminary.domain.models.review_result import ReviewResult
from luminary.domain.prompts.review_prompts import ReviewPromptBuilder, ReviewPromptOptions
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
        comment_mode: str = "both",
        max_context_tokens: Optional[int] = None,
        chunk_overlap_lines: int = 200,
        language: Optional[str] = None,
        framework: Optional[str] = None,
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
        self.comment_mode = comment_mode
        self.max_context_tokens = max_context_tokens
        self.chunk_overlap_lines = chunk_overlap_lines
        self.language = language
        self.framework = framework

    def review_file(self, file_change: FileChange) -> ReviewResult:
        """Review a single file change
        
        Args:
            file_change: File change to review
            
        Returns:
            Review result with comments
        """
        logger.info(f"Reviewing file: {file_change.path}")

        try:
            detected_language = self.language or self._detect_language_from_path(file_change.path)
            responses: List[str] = []
            # Chunk if configured and file is too large
            if self._should_chunk(file_change):
                for chunk_fc, chunk_range in self._iter_file_chunks(file_change):
                    options = ReviewPromptOptions(
                        comment_mode=self.comment_mode,
                        language=detected_language,
                        framework=self.framework,
                        line_number_offset=chunk_range[0] - 1,
                    )
                    logger.debug(
                        f"Calling LLM for file chunk {file_change.path} "
                        f"(lines {chunk_range[0]}-{chunk_range[1]})"
                    )
                    prompt = self.prompt_builder.build(chunk_fc, options=options)
                    responses.append(self.llm_provider.generate(prompt))
            else:
                options = ReviewPromptOptions(
                    comment_mode=self.comment_mode,
                    language=detected_language,
                    framework=self.framework,
                    line_number_offset=0,
                )
                prompt = self.prompt_builder.build(file_change, options=options)
                logger.debug(f"Calling LLM for file: {file_change.path}")
                responses.append(self.llm_provider.generate(prompt))

            # Parse and aggregate
            comments: List[Comment] = []
            summaries: List[str] = []
            for response in responses:
                comments.extend(self._parse_llm_response(response, file_change.path))
                summary = self._extract_summary(response)
                if summary:
                    summaries.append(summary)

            comments = self._dedupe_comments(comments)
            summary_text = self._aggregate_summaries(summaries)

            # Apply comment mode post-processing (defensive, even though prompt requests it)
            if self.comment_mode == "summary":
                comments = []
            elif self.comment_mode == "inline":
                comments = [c for c in comments if c.is_inline]
                summary_text = None

            # Validate comments if validator is provided
            if self.validator:
                original_count = len(comments)
                logger.debug(f"Validating {original_count} comments")
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
                    f"Validation complete: {len(validated_comments)}/{original_count} comments passed"
                )

            # Create review result
            result = ReviewResult(
                file_change=file_change,
                comments=comments,
                summary=summary_text,
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

    def _detect_language_from_path(self, path: str) -> Optional[str]:
        ext = (path.rsplit(".", 1)[-1] if "." in path else "").lower()
        mapping = {
            "py": "Python",
            "js": "JavaScript",
            "ts": "TypeScript",
            "tsx": "TypeScript/React",
            "jsx": "JavaScript/React",
            "java": "Java",
            "kt": "Kotlin",
            "go": "Go",
            "rs": "Rust",
            "cs": "C#",
            "cpp": "C++",
            "c": "C",
            "h": "C/C++ Header",
            "hpp": "C++ Header",
            "php": "PHP",
            "rb": "Ruby",
            "swift": "Swift",
            "scala": "Scala",
            "sql": "SQL",
            "yaml": "YAML",
            "yml": "YAML",
            "json": "JSON",
            "md": "Markdown",
        }
        return mapping.get(ext)

    def _estimate_tokens(self, text: str) -> int:
        # Very rough heuristic: ~4 chars per token on average for code-ish text.
        return max(1, len(text) // 4)

    def _should_chunk(self, file_change: FileChange) -> bool:
        if not self.max_context_tokens:
            return False
        if not file_change.new_content:
            return False
        return self._estimate_tokens(file_change.new_content) > self.max_context_tokens

    def _iter_file_chunks(self, file_change: FileChange) -> Iterable[Tuple[FileChange, Tuple[int, int]]]:
        """Yield FileChange objects for chunks of file_change.new_content.

        Chunking is line-based with overlap; we also filter hunks to those intersecting the chunk.
        """
        assert file_change.new_content is not None
        lines = file_change.new_content.split("\n")

        # Reserve some budget for prompt overhead and diff context; keep code chunk smaller.
        token_budget = int(self.max_context_tokens * 0.7) if self.max_context_tokens else 0
        if token_budget <= 0:
            token_budget = 2000

        # Precompute per-line token estimate
        line_tokens = [self._estimate_tokens(line + "\n") for line in lines]

        start_idx = 0
        while start_idx < len(lines):
            used = 0
            end_idx = start_idx
            while end_idx < len(lines) and used + line_tokens[end_idx] <= token_budget:
                used += line_tokens[end_idx]
                end_idx += 1

            # Ensure progress
            if end_idx == start_idx:
                end_idx = min(len(lines), start_idx + 50)

            chunk_start_line = start_idx + 1
            chunk_end_line = end_idx
            chunk_text = "\n".join(lines[start_idx:end_idx])

            chunk_hunks = self._filter_hunks_for_range(file_change, chunk_start_line, chunk_end_line)

            chunk_fc = FileChange(
                path=file_change.path,
                old_path=file_change.old_path,
                status=file_change.status,
                hunks=chunk_hunks,
                old_content=file_change.old_content,
                new_content=chunk_text,
            )

            yield chunk_fc, (chunk_start_line, chunk_end_line)

            # Next chunk with overlap
            overlap = max(0, self.chunk_overlap_lines)
            next_start = end_idx - overlap if overlap > 0 else end_idx
            # Ensure progress even if overlap is larger than the produced chunk
            start_idx = max(next_start, start_idx + 1)

            if start_idx >= len(lines):
                break

    def _filter_hunks_for_range(
        self, file_change: FileChange, start_line: int, end_line: int
    ) -> List:
        if not file_change.hunks:
            return []
        filtered = []
        for hunk in file_change.hunks:
            hunk_start = hunk.new_start
            hunk_end = hunk.new_start + max(0, hunk.new_count) - 1
            # If range overlaps
            if hunk_end >= start_line and hunk_start <= end_line:
                filtered.append(hunk)
        return filtered

    def _dedupe_comments(self, comments: List[Comment]) -> List[Comment]:
        seen = set()
        out: List[Comment] = []
        for c in comments:
            key = (
                c.file_path,
                c.line_number,
                c.line_range,
                c.severity,
                re.sub(r"\s+", " ", (c.content or "").strip().lower()),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
        return out

    def _aggregate_summaries(self, summaries: List[str]) -> Optional[str]:
        if not summaries:
            return None
        if len(summaries) == 1:
            return summaries[0]
        # Keep it readable for MR summary aggregation downstream
        lines = []
        for i, s in enumerate(summaries, 1):
            lines.append(f"Chunk {i} summary:")
            lines.append(s)
            lines.append("")
        return "\n".join(lines).strip()

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
        # Debug: log raw response to understand what LLM returns
        logger.debug(f"Parsing LLM response for {file_path} (length: {len(response)} chars, first 500 chars: {response[:500]!r})")
        
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
            logger.debug(f"No inline comments found in LLM response for {file_path}. Response doesn't contain 'Line X:' format. Creating general comment.")
            comments.append(
                Comment(
                    content=response,
                    file_path=file_path,
                    severity=Severity.INFO,
                )
            )
        else:
            logger.debug(f"Parsed {len(comments)} inline comments from LLM response for {file_path}")

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
