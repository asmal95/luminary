"""Comment validator using LLM"""

import json
import logging
from typing import List, Optional

from luminary.domain.models.comment import Comment
from luminary.domain.models.file_change import FileChange
from luminary.domain.prompts.validation_prompts import ValidationPromptBuilder
from luminary.infrastructure.llm.base import LLMProvider

logger = logging.getLogger(__name__)


class ValidationResult:
    """Result of comment validation"""

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

            # Parse response
            result = self._parse_validation_response(response, comment)

            # Update stats
            if result.valid:
                self.stats["valid"] += 1
            else:
                self.stats["invalid"] += 1
                logger.info(
                    f"Comment rejected: {result.reason} "
                    f"(scores: {result.scores})"
                )

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
        # Try to extract JSON from response
        try:
            # Look for JSON block
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = response[start:end]
                data = json.loads(json_str)
            else:
                # Try parsing entire response
                data = json.loads(response)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse validation response as JSON: {e}")
            # Fallback: check if response contains "valid: true/false"
            response_lower = response.lower()
            if "valid: true" in response_lower or '"valid": true' in response_lower:
                valid = True
            elif "valid: false" in response_lower or '"valid": false' in response_lower:
                valid = False
            else:
                # Default to invalid if can't parse
                valid = False

            return ValidationResult(
                valid=valid,
                reason="Could not parse validation response",
                scores={"relevance": 0.5, "usefulness": 0.5, "non_redundancy": 0.5},
                comment=comment,
            )

        # Extract validation data
        valid = data.get("valid", False)
        reason = data.get("reason", "No reason provided")
        scores = data.get("scores", {})

        # Check threshold
        relevance = scores.get("relevance", 0.0)
        usefulness = scores.get("usefulness", 0.0)
        non_redundancy = scores.get("non_redundancy", 0.0)

        # Comment is valid if all scores meet threshold
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
