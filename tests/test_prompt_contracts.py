import json

import pytest

from luminary.domain.models.comment import Comment
from luminary.domain.models.file_change import FileChange
from luminary.domain.prompts.review_prompts import ReviewPromptBuilder, ReviewPromptOptions
from luminary.domain.prompts.validation_prompts import ValidationPromptBuilder
from luminary.domain.validators.comment_validator import CommentValidator
from luminary.infrastructure.llm.base import LLMProvider


class EchoingValidationProvider(LLMProvider):
    def __init__(self, response_factory):
        super().__init__({})
        self.response_factory = response_factory

    def generate(self, prompt: str, **kwargs) -> str:
        return self.response_factory(prompt)


def test_review_prompt_contract_keeps_mode_markers_and_json_shape():
    builder = ReviewPromptBuilder()
    file_change = FileChange(path="src/test.py", new_content="print('hi')\n")

    inline_prompt = builder.build(file_change, ReviewPromptOptions(comment_mode="inline"))
    assert "inline comments only" in inline_prompt.lower()
    assert '"line" field MUST be a number' in inline_prompt
    assert "Return ONLY JSON" in inline_prompt

    summary_prompt = builder.build(file_change, ReviewPromptOptions(comment_mode="summary"))
    assert "summary only" in summary_prompt.lower()
    assert '{"comments": [...], "summary": "text"}' in summary_prompt


def test_validation_prompt_contract_uses_placeholders_without_hardcoded_threshold():
    builder = ValidationPromptBuilder()
    file_change = FileChange(path="src/test.py", new_content="x = 1\n")
    comment = Comment(content="Consider null-check", line_number=1, file_path="src/test.py")

    prompt = builder.build(comment, file_change)
    assert "Code context:" in prompt
    assert "Comment:" in prompt
    assert "<true_or_false>" in prompt
    assert "<0_to_1>" in prompt
    assert "Valid if all >= 0.7" not in prompt


def test_comment_validator_strips_echoed_prompt_and_parses_json():
    provider = EchoingValidationProvider(
        lambda prompt: (
            f"{prompt}\n"
            + json.dumps(
                {
                    "valid": True,
                    "reason": "Looks good",
                    "scores": {"relevance": 0.9, "usefulness": 0.9, "non_redundancy": 0.9},
                }
            )
        )
    )
    validator = CommentValidator(provider, threshold=0.7)

    comment = Comment(content="Use safer API", line_number=1, file_path="src/test.py")
    file_change = FileChange(path="src/test.py", new_content="dangerous_call()\n")
    result = validator.validate(comment, file_change)

    assert result.valid is True
    assert result.reason == "Looks good"


def test_comment_validator_marks_unparseable_response_invalid():
    provider = EchoingValidationProvider(lambda _: "not json at all")
    validator = CommentValidator(provider, threshold=0.7)

    comment = Comment(content="Test", line_number=1, file_path="src/test.py")
    file_change = FileChange(path="src/test.py", new_content="x = 1\n")
    result = validator.validate(comment, file_change)

    assert result.valid is False
    assert "Parse failed" in result.reason
    assert result.scores["relevance"] == 0.0


def test_review_prompt_requires_context_placeholder():
    with pytest.raises(ValueError, match="\\{context\\}"):
        ReviewPromptBuilder(custom_prompt="No placeholders here")


def test_validation_prompt_requires_required_placeholders():
    with pytest.raises(ValueError, match="\\{code_context\\}"):
        ValidationPromptBuilder(custom_prompt="Comment: {comment}")

