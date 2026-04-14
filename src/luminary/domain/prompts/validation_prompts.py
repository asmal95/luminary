"""Validation prompt templates"""

from typing import Optional

from luminary.domain.models.comment import Comment
from luminary.domain.models.file_change import FileChange


class ValidationPromptBuilder:
    """Builder for comment validation prompts"""

    DEFAULT_VALIDATION_PROMPT = """Task: Evaluate one code review comment and return JSON.

Code context:
{code_context}

Comment:
{comment}

Instructions:
1. Rate relevance (0.0-1.0): Does comment relate to code?
2. Rate usefulness (0.0-1.0): Is it actionable and technically sound?
3. Rate non_redundancy (0.0-1.0): Does it add non-obvious value?
4. Set "valid" to true only when the comment is relevant, useful, and non-redundant.
5. If context is insufficient to verify claims, lower scores and explain uncertainty in "reason".

OUTPUT FORMAT - Return EXACTLY this JSON structure, nothing else:
{{"valid": <true_or_false>, "reason": "<short_reason>", "scores": {{"relevance": <0_to_1>, "usefulness": <0_to_1>, "non_redundancy": <0_to_1>}}}}

DO NOT write any code, explanations, or text. ONLY return the JSON object above with your evaluation."""

    def __init__(self, custom_prompt: Optional[str] = None):
        """Initialize validation prompt builder

        Args:
            custom_prompt: Custom prompt template (uses default if None)
        """
        self.template = custom_prompt or self.DEFAULT_VALIDATION_PROMPT
        required_placeholders = ("{code_context}", "{comment}")
        missing = [placeholder for placeholder in required_placeholders if placeholder not in self.template]
        if missing:
            missing_list = ", ".join(missing)
            raise ValueError(
                f"Validation prompt template must include required placeholders: {missing_list}"
            )

    def build(
        self, comment: Comment, file_change: FileChange, code_snippet: Optional[str] = None
    ) -> str:
        """Build validation prompt for comment

        Args:
            comment: Comment to validate
            file_change: File change context
            code_snippet: Relevant code snippet (if available)

        Returns:
            Formatted prompt string
        """
        # Build code context
        context_parts = [f"File: {file_change.path}"]

        if code_snippet:
            context_parts.append("\nRelevant code:")
            context_parts.append("```")
            context_parts.append(code_snippet)
            context_parts.append("```")
        elif file_change.new_content:
            # Extract relevant lines if comment has line number
            if comment.line_number:
                lines = file_change.new_content.split("\n")
                start = max(0, comment.line_number - 5)
                end = min(len(lines), comment.line_number + 5)
                context_parts.append(f"\nRelevant code (around line {comment.line_number}):")
                context_parts.append("```")
                for i in range(start, end):
                    marker = ">>> " if i == comment.line_number - 1 else "    "
                    context_parts.append(f"{marker}{i+1}: {lines[i]}")
                context_parts.append("```")
            else:
                context_parts.append("\nFile content:")
                context_parts.append("```")
                # Limit size
                content = file_change.new_content
                if len(content) > 500:
                    content = content[:500] + "\n... (truncated) ..."
                context_parts.append(content)
                context_parts.append("```")

        code_context = "\n".join(context_parts)

        # Format prompt
        prompt = self.template.format(
            code_context=code_context,
            comment=comment.content,
        )

        return prompt
