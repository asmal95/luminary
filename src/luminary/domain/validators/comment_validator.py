"""Comment validator using LLM"""

import json
import logging
import re
from typing import Any, Dict, Optional

from luminary.domain.models.comment import Comment
from luminary.domain.models.file_change import FileChange
from luminary.domain.prompts.validation_prompts import ValidationPromptBuilder
from luminary.infrastructure.llm.base import LLMProvider

logger = logging.getLogger(__name__)


class ValidationResult:
    """Result of comment validation"""

    valid: bool
    reason: str
    scores: Dict[str, float]
    comment: Comment

    def __init__(
        self,
        valid: bool,
        reason: str,
        scores: dict,
        comment: Comment,
    ):
        """Initialize validation result

        Args:
            valid: Whether comment is valid
            reason: Reason for validation decision
            scores: Scores for each criterion
            comment: The validated comment
        """
        self.valid = valid
        self.reason = reason
        self.scores = scores
        self.comment = comment


class CommentValidator:
    """Validator for code review comments using LLM"""

    DEFAULT_THRESHOLD = 0.7

    llm_provider: LLMProvider
    threshold: float
    prompt_builder: ValidationPromptBuilder
    stats: Dict[str, Any]

    def __init__(
        self,
        llm_provider: LLMProvider,
        threshold: float = DEFAULT_THRESHOLD,
        custom_prompt: Optional[str] = None,
    ):
        """Initialize comment validator

        Args:
            llm_provider: LLM provider for validation
            threshold: Minimum score threshold (default: 0.7)
            custom_prompt: Custom validation prompt template
        """
        self.llm_provider = llm_provider
        self.threshold = threshold
        self.prompt_builder = ValidationPromptBuilder(custom_prompt)
        self.stats = {
            "total": 0,
            "valid": 0,
            "invalid": 0,
            "errors": 0,
            "score_sums": {"relevance": 0.0, "usefulness": 0.0, "non_redundancy": 0.0},
            "score_count": 0,
        }

    def validate(
        self, comment: Comment, file_change: FileChange, code_snippet: Optional[str] = None
    ) -> ValidationResult:
        """Validate a comment

        Args:
            comment: Comment to validate
            file_change: File change context
            code_snippet: Relevant code snippet (optional)

        Returns:
            ValidationResult with validation decision and scores
        """
        self.stats["total"] += 1

        try:
            # Build validation prompt
            prompt = self.prompt_builder.build(comment, file_change, code_snippet)

            # Get LLM response
            logger.debug(f"Validating comment for {file_change.path}:{comment.line_number}")
            response = self.llm_provider.generate(prompt)

            # Log the raw response for debugging (truncated)
            logger.debug(f"Raw validation response (first 500 chars): {response[:500]}")

            # Check if response contains the prompt (LLM sometimes echoes back the prompt)
            # This happens when LLM returns prompt + response instead of just response
            prompt_starters = [
                "You are a validator for code review comments",
                "You are Qwen",
                "You are an expert code reviewer",
                "Evaluate this code review comment",
            ]

            # If response starts with prompt text, remove it
            for starter in prompt_starters:
                if response.startswith(starter):
                    # Find the first JSON object start
                    json_start = response.find("{")
                    if json_start > 50:  # JSON appears later in response (after prompt)
                        response = response[json_start:].strip()
                        break

            # Also check if entire prompt is echoed back
            if response.startswith(prompt[:150]):  # Check first 150 chars
                # Extract only the response part (after prompt)
                response = response[len(prompt) :].strip()

            # If response still contains prompt text (multiline), try to extract JSON
            if any(starter in response for starter in prompt_starters):
                # Find JSON boundaries - look for first { and last }
                json_start = response.find("{")
                if json_start >= 0:
                    json_end = response.rfind("}")
                    if json_end > json_start:
                        # Extract just the JSON part
                        potential_json = response[json_start : json_end + 1]
                        # Quick validation: does it look like JSON?
                        if potential_json.strip().startswith(
                            "{"
                        ) and potential_json.strip().endswith("}"):
                            response = potential_json.strip()

            # Parse response
            result = self._parse_validation_response(response, comment)

            # Update stats
            if result.valid:
                self.stats["valid"] += 1
            else:
                self.stats["invalid"] += 1
                logger.info(f"Comment rejected: {result.reason} " f"(scores: {result.scores})")

            # Update score aggregates when present
            try:
                scores = result.scores or {}
                for k in ("relevance", "usefulness", "non_redundancy"):
                    if isinstance(scores.get(k), (int, float)):
                        self.stats["score_sums"][k] += float(scores[k])
                self.stats["score_count"] += 1
            except Exception:
                # Don't fail validation due to stats aggregation
                pass

            return result

        except Exception as e:
            self.stats["errors"] += 1
            logger.error(f"Error validating comment: {e}", exc_info=True)
            # On error, default to invalid
            return ValidationResult(
                valid=False,
                reason=f"Validation error: {e}",
                scores={"relevance": 0.0, "usefulness": 0.0, "non_redundancy": 0.0},
                comment=comment,
            )

    def _parse_validation_response(self, response: str, comment: Comment) -> ValidationResult:
        """Parse LLM validation response

        Args:
            response: LLM response text
            comment: Comment being validated

        Returns:
            ValidationResult
        """
        if not response or not response.strip():
            logger.warning("Empty validation response, assuming valid")
            return self._create_fallback_result(comment, "Empty response")

        # Extract JSON from response
        json_str = self._extract_json_from_response(response)
        if not json_str:
            return self._handle_unparseable_response(response, comment)

        # Parse JSON with fixes
        data = self._parse_json_with_fixes(json_str)
        if not data:
            return self._handle_unparseable_response(response, comment)

        # Create validation result
        return self._create_validation_result(data, comment)

    def _extract_json_from_response(self, response: str) -> Optional[str]:
        """Extract JSON string from response

        Args:
            response: LLM response text

        Returns:
            Extracted JSON string or None
        """
        # Strategy 1: Extract from markdown code blocks (```json or ```)
        code_block_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
        if code_block_match:
            return code_block_match.group(1)

        # Strategy 2: Find complete JSON object with validation structure
        brace_start = -1
        brace_depth = 0

        for i, char in enumerate(response):
            if char == "{":
                if brace_depth == 0:
                    brace_start = i
                brace_depth += 1
            elif char == "}":
                brace_depth -= 1
                if brace_depth == 0 and brace_start >= 0:
                    candidate = response[brace_start : i + 1]
                    # Check if it has expected structure
                    if '"valid"' in candidate and '"scores"' in candidate:
                        return candidate

        return None

    def _parse_json_with_fixes(self, json_str: str) -> Optional[dict]:
        """Parse JSON with common fixes

        Args:
            json_str: JSON string to parse

        Returns:
            Parsed dictionary or None
        """
        # Attempt 1: Direct parse
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

        # Attempt 2: Fix common issues
        try:
            # Remove trailing commas
            fixed_json = re.sub(r",(\s*[}\]])", r"\1", json_str)
            # Fix single quotes to double quotes
            fixed_json = re.sub(r"'(\w+)':", r'"\1":', fixed_json)
            return json.loads(fixed_json)
        except (json.JSONDecodeError, ValueError):
            pass

        return None

    def _handle_unparseable_response(self, response: str, comment: Comment) -> ValidationResult:
        """Handle unparseable response with fallback

        Args:
            response: Original response
            comment: Comment being validated

        Returns:
            Fallback ValidationResult
        """
        response_lower = response.strip().lower()

        # Simple text-based fallback
        if any(x in response_lower for x in ["valid: true", '"valid": true', "^true"]):
            return self._create_fallback_result(comment, "Text parse: valid=true", valid=True)
        elif any(x in response_lower for x in ["valid: false", '"valid": false']):
            return self._create_fallback_result(comment, "Text parse: valid=false", valid=False)

        # Default to valid (better to accept than reject on parsing errors)
        logger.warning("Could not parse validation response, assuming valid")
        logger.debug(f"Unparseable response (first 200 chars): {response[:200]}")
        return self._create_fallback_result(comment, "Parse failed, assuming valid")

    def _create_fallback_result(
        self, comment: Comment, reason: str, valid: bool = True
    ) -> ValidationResult:
        """Create fallback validation result

        Args:
            comment: Comment being validated
            reason: Fallback reason
            valid: Whether to consider valid (default: True)

        Returns:
            ValidationResult with default scores
        """
        return ValidationResult(
            valid=valid,
            reason=reason,
            scores={"relevance": 0.7, "usefulness": 0.7, "non_redundancy": 0.7},
            comment=comment,
        )

    def _create_validation_result(self, data: dict, comment: Comment) -> ValidationResult:
        """Create validation result from parsed data

        Args:
            data: Parsed JSON data
            comment: Comment being validated

        Returns:
            ValidationResult with threshold check
        """
        valid = data.get("valid", False)
        reason = data.get("reason", "No reason provided")
        scores = data.get("scores", {})

        # Extract scores
        relevance = scores.get("relevance", 0.0)
        usefulness = scores.get("usefulness", 0.0)
        non_redundancy = scores.get("non_redundancy", 0.0)

        # Check threshold
        is_valid = (
            valid
            and relevance >= self.threshold
            and usefulness >= self.threshold
            and non_redundancy >= self.threshold
        )

        return ValidationResult(
            valid=is_valid,
            reason=reason,
            scores=scores,
            comment=comment,
        )

    def get_stats(self) -> dict:
        """Get validation statistics

        Returns:
            Dictionary with validation statistics
        """
        stats = self.stats.copy()
        count = stats.get("score_count", 0) or 0
        if count > 0:
            sums = stats.get("score_sums", {})
            stats["score_avgs"] = {
                "relevance": sums.get("relevance", 0.0) / count,
                "usefulness": sums.get("usefulness", 0.0) / count,
                "non_redundancy": sums.get("non_redundancy", 0.0) / count,
            }
        return stats
