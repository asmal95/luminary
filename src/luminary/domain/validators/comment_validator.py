"""Comment validator using LLM"""

import json
import logging
import re
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

            # Check if response contains the prompt (LLM sometimes echoes back the prompt)
            # This happens when LLM returns prompt + response instead of just response
            prompt_starters = [
                "You are a validator for code review comments",
                "You are Qwen",
                "You are an expert code reviewer",
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
                response = response[len(prompt):].strip()
            
            # If response still contains prompt text (multiline), try to extract JSON
            if any(starter in response for starter in prompt_starters):
                # Find JSON boundaries - look for first { and last }
                json_start = response.find("{")
                if json_start >= 0:
                    json_end = response.rfind("}")
                    if json_end > json_start:
                        # Extract just the JSON part
                        potential_json = response[json_start:json_end + 1]
                        # Quick validation: does it look like JSON?
                        if potential_json.strip().startswith("{") and potential_json.strip().endswith("}"):
                            response = potential_json.strip()

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
        if not response or not response.strip():
            logger.warning("Empty validation response, assuming valid")
            return ValidationResult(
                valid=True,
                reason="Empty validation response (using fallback)",
                scores={"relevance": 0.7, "usefulness": 0.7, "non_redundancy": 0.7},
                comment=comment,
            )
        
        # Strategy 1: Try to extract JSON from markdown code blocks
        json_str = None
        code_block_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
        if code_block_match:
            json_str = code_block_match.group(1)
        
        # Strategy 2: Look for JSON object boundaries
        if not json_str:
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = response[start:end]
        
        # Strategy 3: Try entire response
        if not json_str:
            json_str = response.strip()
        
        # Try multiple parsing strategies
        data = None
        parsing_errors = []
        
        # Attempt 1: Direct parse
        if json_str:
            try:
                data = json.loads(json_str)
            except json.JSONDecodeError as e:
                parsing_errors.append(f"Direct parse: {e}")
                
                # Attempt 2: Try to fix common JSON issues
                try:
                    # Remove trailing commas before closing braces/brackets
                    fixed_json = re.sub(r',(\s*[}\]])', r'\1', json_str)
                    # Fix single quotes to double quotes (basic)
                    fixed_json = re.sub(r"'(\w+)':", r'"\1":', fixed_json)
                    data = json.loads(fixed_json)
                except (json.JSONDecodeError, ValueError) as e2:
                    parsing_errors.append(f"Fixed parse: {e2}")
                    
                    # Attempt 3: Try to extract just the values we need
                    try:
                        # Use regex to extract key-value pairs
                        valid_match = re.search(r'"valid"\s*:\s*(true|false)', json_str, re.IGNORECASE)
                        reason_match = re.search(r'"reason"\s*:\s*"([^"]*)"', json_str)
                        
                        if valid_match or reason_match:
                            valid = valid_match.group(1).lower() == "true" if valid_match else True
                            reason = reason_match.group(1) if reason_match else "Partial parse"
                            
                            # Try to extract scores
                            scores = {}
                            for score_key in ["relevance", "usefulness", "non_redundancy"]:
                                score_match = re.search(
                                    rf'"{score_key}"\s*:\s*([0-9.]+)',
                                    json_str,
                                    re.IGNORECASE
                                )
                                if score_match:
                                    try:
                                        scores[score_key] = float(score_match.group(1))
                                    except ValueError:
                                        scores[score_key] = 0.7
                            
                            if not scores:
                                scores = {"relevance": 0.7, "usefulness": 0.7, "non_redundancy": 0.7}
                            
                            return ValidationResult(
                                valid=valid,
                                reason=reason,
                                scores=scores,
                                comment=comment,
                            )
                    except Exception as e3:
                        parsing_errors.append(f"Regex extract: {e3}")
        
        # If we successfully parsed JSON
        if data:
            valid = data.get("valid", False)
            reason = data.get("reason", "No reason provided")
            scores = data.get("scores", {})
        else:
            # Fallback: check if response contains "valid: true/false"
            response_lower = response.strip().lower()
            if "valid: true" in response_lower or '"valid": true' in response_lower or response_lower.startswith("true"):
                valid = True
                reason = "Fallback parse: found valid=true"
            elif "valid: false" in response_lower or '"valid": false' in response_lower or response_lower.startswith("false"):
                valid = False
                reason = "Fallback parse: found valid=false"
            else:
                # Default to valid if can't parse (better to accept than reject on parsing errors)
                logger.warning(
                    f"Could not parse validation response after {len(parsing_errors)} attempts. "
                    f"Errors: {parsing_errors}. Response preview: {response[:200]}"
                )
                valid = True
                reason = "Could not parse validation response (using fallback: assuming valid)"
            
            scores = {"relevance": 0.7, "usefulness": 0.7, "non_redundancy": 0.7}
            
            return ValidationResult(
                valid=valid,
                reason=reason,
                scores=scores,
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
