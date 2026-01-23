"""Review service - orchestrates code review process"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from luminary.domain.models.comment import Comment, Severity
from luminary.domain.models.file_change import FileChange
from luminary.domain.models.review_result import ReviewResult
from luminary.domain.prompts.review_prompts import ReviewPromptBuilder, ReviewPromptOptions
from luminary.domain.validators.comment_validator import CommentValidator
from luminary.infrastructure.llm.base import LLMProvider

logger = logging.getLogger(__name__)


class ReviewService:
    """Service for reviewing code changes"""

    llm_provider: LLMProvider
    validator: Optional[CommentValidator]
    prompt_builder: ReviewPromptBuilder
    comment_mode: str
    max_context_tokens: Optional[int]
    chunk_overlap_lines: int
    language: Optional[str]
    framework: Optional[str]

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
            comment_mode: Comment mode ("inline", "summary", or "both")
            max_context_tokens: Maximum context tokens (enables chunking if exceeded)
            chunk_overlap_lines: Number of lines to overlap between chunks
            language: Explicit language (overrides auto-detection)
            framework: Framework name (e.g., "Django", "React")
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
            responses = self._get_llm_responses(file_change, detected_language)

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

            # Apply comment mode post-processing
            comments, summary_text = self._apply_comment_mode(comments, summary_text)

            # Validate comments if validator is provided
            if self.validator:
                comments = self._validate_comments(comments, file_change)

            result = ReviewResult(
                file_change=file_change,
                comments=comments,
                summary=summary_text,
            )

            logger.info(
                f"Review completed for {file_change.path}: " f"{len(comments)} comments generated"
            )
            return result

        except Exception as e:
            logger.error(f"Error reviewing file {file_change.path}: {e}", exc_info=True)
            return ReviewResult(file_change=file_change, error=str(e))

    def _get_llm_responses(self, file_change: FileChange, language: Optional[str]) -> List[str]:
        """Get LLM responses (with chunking if needed)

        Args:
            file_change: File change to review
            language: Detected language

        Returns:
            List of LLM response strings
        """
        responses: List[str] = []

        if self._should_chunk(file_change):
            for chunk_fc, chunk_range in self._iter_file_chunks(file_change):
                options = ReviewPromptOptions(
                    comment_mode=self.comment_mode,
                    language=language,
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
                language=language,
                framework=self.framework,
                line_number_offset=0,
            )
            prompt = self.prompt_builder.build(file_change, options=options)
            logger.debug(f"Calling LLM for file: {file_change.path}")
            responses.append(self.llm_provider.generate(prompt))

        return responses

    def _apply_comment_mode(
        self, comments: List[Comment], summary: Optional[str]
    ) -> Tuple[List[Comment], Optional[str]]:
        """Apply comment mode filtering

        Args:
            comments: List of comments
            summary: Summary text

        Returns:
            Tuple of (filtered comments, filtered summary)
        """
        if self.comment_mode == "summary":
            return [], summary
        elif self.comment_mode == "inline":
            return [c for c in comments if c.is_inline], None
        else:  # both
            return comments, summary

    def _validate_comments(self, comments: List[Comment], file_change: FileChange) -> List[Comment]:
        """Validate comments using validator

        Args:
            comments: List of comments to validate
            file_change: File change for context

        Returns:
            List of validated comments
        """
        if not self.validator:
            return comments

        original_count = len(comments)
        logger.debug(f"Validating {original_count} comments")
        validated_comments = []

        for comment in comments:
            code_snippet = self._extract_code_snippet(file_change, comment)
            validation_result = self.validator.validate(comment, file_change, code_snippet)
            if validation_result.valid:
                validated_comments.append(comment)
            else:
                logger.debug(
                    f"Comment rejected: {validation_result.reason} "
                    f"(scores: {validation_result.scores})"
                )

        logger.info(
            f"Validation complete: {len(validated_comments)}/{original_count} comments passed"
        )
        return validated_comments

    def _extract_json_from_response(self, response: str) -> Optional[str]:
        """Extract JSON string from response (handles markdown code blocks)

        Args:
            response: LLM response text

        Returns:
            JSON string or None if not found
        """
        json_str = response.strip()

        # Try to extract JSON from markdown code blocks
        json_match = re.search(r'```(?:json)?\s*(\[.*?\]|{.*?"comments".*?})', response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)

        return json_str

    def _fix_common_json_errors(self, json_str: str) -> str:
        """Fix common JSON errors before parsing

        Args:
            json_str: JSON string with potential errors

        Returns:
            Fixed JSON string
        """
        # Fix empty line fields: "line": , -> "line": null
        json_str = re.sub(r'"line"\s*:\s*,', '"line": null,', json_str)

        # Fix empty suggestion fields
        json_str = re.sub(r'"suggestion"\s*:\s*,', '"suggestion": null,', json_str)
        json_str = re.sub(r'"suggestion"\s*:\s*}', '"suggestion": null}', json_str)
        json_str = re.sub(
            r'"suggestion"\s*:\s*$', '"suggestion": null', json_str, flags=re.MULTILINE
        )

        # Remove trailing commas before closing brackets/braces
        json_str = re.sub(r",(\s*[}\]])", r"\1", json_str)

        # Fix unquoted null values
        json_str = re.sub(r":\s*null\b", ": null", json_str)

        # Fix missing quotes around string values (conservative heuristic)
        json_str = re.sub(
            r'"message"\s*:\s*([^",\[\]{}()\n]+)(?=\s*[,}])', r'"message": "\1"', json_str
        )

        return json_str

    def _parse_json_response(self, json_str: str) -> Optional[Dict[str, Any]]:
        """Parse JSON string with error handling

        Args:
            json_str: JSON string to parse

        Returns:
            Parsed JSON data or None if parsing fails
        """
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            # Try to find JSON object with "comments" field
            obj_match = re.search(r'({[^{}]*"comments"[^{}]*})', json_str, re.DOTALL)
            if obj_match:
                try:
                    fixed_str = self._fix_common_json_errors(obj_match.group(1))
                    return json.loads(fixed_str)
                except json.JSONDecodeError:
                    pass
        return None

    def _parse_comment_item(self, item: Dict[str, Any], file_path: str) -> Optional[Comment]:
        """Parse a single comment item from JSON

        Args:
            item: Comment item dictionary
            file_path: Expected file path

        Returns:
            Comment object or None if invalid
        """
        if not isinstance(item, dict):
            logger.warning(f"Skipping invalid comment item (not a dict): {item}")
            return None

        comment_line = item.get("line")
        if comment_line is None:
            logger.warning(f"Skipping comment without line number: {item}")
            return None

        # Validate and convert line number
        try:
            if isinstance(comment_line, str):
                comment_line = comment_line.strip()
                if not comment_line:
                    logger.warning(f"Skipping comment with empty line number: {item}")
                    return None
            line_number = int(comment_line)
            if line_number < 1:
                logger.warning(f"Invalid line number {line_number} (must be >= 1)")
                return None
        except (ValueError, TypeError) as e:
            logger.warning(f"Invalid line number format '{comment_line}': {e}")
            return None

        # Extract fields
        comment_message = item.get("message", "")
        comment_suggestion = item.get("suggestion")

        # Determine severity from message keywords
        severity = self._infer_severity(comment_message)

        return Comment(
            content=comment_message,
            line_number=line_number,
            file_path=file_path,
            severity=severity,
            suggestion=comment_suggestion if comment_suggestion else None,
        )

    def _infer_severity(self, message: str) -> Severity:
        """Infer severity from message content

        Args:
            message: Comment message

        Returns:
            Inferred severity level
        """
        message_lower = message.lower()
        if any(keyword in message_lower for keyword in ["error", "critical", "bug"]):
            return Severity.ERROR
        elif any(keyword in message_lower for keyword in ["warning", "potential"]):
            return Severity.WARNING
        return Severity.INFO

    def _parse_llm_response(self, response: str, file_path: str) -> List[Comment]:
        """Parse LLM response into Comment objects

        Args:
            response: LLM response text (should be JSON)
            file_path: Path to the file being reviewed

        Returns:
            List of Comment objects
        """
        logger.debug(f"Parsing LLM response for {file_path} " f"(length: {len(response)} chars)")

        try:
            # Extract and fix JSON
            json_str = self._extract_json_from_response(response)
            if not json_str:
                raise ValueError("Could not extract JSON from response")

            fixed_json = self._fix_common_json_errors(json_str)
            data = self._parse_json_response(fixed_json)

            if data is None:
                raise ValueError("Could not parse JSON from response")

            # Handle both array format and object format
            if isinstance(data, list):
                comments_array = data
            elif isinstance(data, dict):
                comments_array = data.get("comments", [])
            else:
                raise ValueError(f"Unexpected JSON structure: {type(data)}")

            # Parse comments
            comments = []
            for item in comments_array:
                comment = self._parse_comment_item(item, file_path)
                if comment:
                    comments.append(comment)

            logger.debug(f"Parsed {len(comments)} comments from JSON response for {file_path}")
            return comments

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(
                f"Failed to parse JSON response for {file_path}: {e}. "
                f"Response preview: {response[:200]}..."
            )
            return self._create_fallback_comment(
                response, file_path, "Parsing error: expected JSON format"
            )

        except Exception as e:
            logger.error(f"Error parsing LLM response for {file_path}: {e}", exc_info=True)
            return self._create_fallback_comment(response, file_path, "Error parsing response")

    def _create_fallback_comment(
        self, response: str, file_path: str, error_prefix: str
    ) -> List[Comment]:
        """Create fallback comment when parsing fails

        Args:
            response: Original response text
            file_path: File path
            error_prefix: Error message prefix

        Returns:
            List with single fallback comment
        """
        if response.strip():
            return [
                Comment(
                    content=f"*[{error_prefix}]*\n\n{response}",
                    file_path=file_path,
                    severity=Severity.INFO,
                )
            ]
        return []

    def _extract_summary(self, response: str) -> Optional[str]:
        """Extract summary section from LLM response

        Args:
            response: LLM response text (JSON or text)

        Returns:
            Summary text or None
        """
        # Try to extract summary from JSON first
        try:
            json_str = self._extract_json_from_response(response)
            if json_str:
                fixed_json = self._fix_common_json_errors(json_str)
                data = self._parse_json_response(fixed_json)

                if data and isinstance(data, dict) and "summary" in data:
                    summary = data.get("summary")
                    if summary:
                        return summary
        except Exception:
            pass

        # Fallback to legacy text format parsing
        return self._extract_summary_from_text(response)

    def _extract_summary_from_text(self, response: str) -> Optional[str]:
        """Extract summary from legacy text format

        Args:
            response: Response text

        Returns:
            Summary text or None
        """
        lines = response.split("\n")
        summary_started = False
        summary_lines = []

        for line in lines:
            if "**Summary:**" in line or line.strip().startswith("Summary:"):
                summary_started = True
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

    def _detect_language_from_path(self, path: str) -> Optional[str]:
        """Detect programming language from file path

        Args:
            path: File path

        Returns:
            Language name or None
        """
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
        """Estimate token count (rough heuristic: ~4 chars per token)

        Args:
            text: Text to estimate

        Returns:
            Estimated token count
        """
        return max(1, len(text) // 4)

    def _should_chunk(self, file_change: FileChange) -> bool:
        """Check if file should be chunked

        Args:
            file_change: File change to check

        Returns:
            True if chunking is needed
        """
        if not self.max_context_tokens or not file_change.new_content:
            return False
        return self._estimate_tokens(file_change.new_content) > self.max_context_tokens

    def _iter_file_chunks(
        self, file_change: FileChange
    ) -> Iterable[Tuple[FileChange, Tuple[int, int]]]:
        """Yield FileChange objects for chunks of file

        Chunking is line-based with overlap; hunks are filtered to those intersecting the chunk.

        Args:
            file_change: File change to chunk

        Yields:
            Tuple of (chunk FileChange, (start_line, end_line))
        """
        assert file_change.new_content is not None
        lines = file_change.new_content.split("\n")

        # Reserve budget for prompt overhead; keep code chunk smaller
        token_budget = int(self.max_context_tokens * 0.7) if self.max_context_tokens else 2000

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
            chunk_hunks = self._filter_hunks_for_range(
                file_change, chunk_start_line, chunk_end_line
            )

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
            start_idx = max(next_start, start_idx + 1)

            if start_idx >= len(lines):
                break

    def _filter_hunks_for_range(
        self, file_change: FileChange, start_line: int, end_line: int
    ) -> List:
        """Filter hunks to those intersecting the given line range

        Args:
            file_change: File change with hunks
            start_line: Start line number (1-based)
            end_line: End line number (1-based)

        Returns:
            List of hunks intersecting the range
        """
        if not file_change.hunks:
            return []

        filtered = []
        for hunk in file_change.hunks:
            hunk_start = hunk.new_start
            hunk_end = hunk.new_start + max(0, hunk.new_count) - 1
            # Check if ranges overlap
            if hunk_end >= start_line and hunk_start <= end_line:
                filtered.append(hunk)
        return filtered

    def _dedupe_comments(self, comments: List[Comment]) -> List[Comment]:
        """Remove duplicate comments

        Args:
            comments: List of comments

        Returns:
            Deduplicated list of comments
        """
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
            if key not in seen:
                seen.add(key)
                out.append(c)
        return out

    def _aggregate_summaries(self, summaries: List[str]) -> Optional[str]:
        """Aggregate multiple summaries into one

        Args:
            summaries: List of summary strings

        Returns:
            Aggregated summary or None
        """
        if not summaries:
            return None
        if len(summaries) == 1:
            return summaries[0]

        # Format multiple summaries
        lines = []
        for i, s in enumerate(summaries, 1):
            lines.append(f"Chunk {i} summary:")
            lines.append(s)
            lines.append("")
        return "\n".join(lines).strip()

    def _extract_code_snippet(self, file_change: FileChange, comment: Comment) -> Optional[str]:
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
            return "\n".join(lines[start:end])

        return None
