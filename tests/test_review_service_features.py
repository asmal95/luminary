import json
import re

from luminary.application.review_service import ReviewService
from luminary.domain.models.file_change import FileChange
from luminary.infrastructure.llm.base import LLMProvider


class DummyCountingProvider(LLMProvider):
    """Deterministic provider for testing review pipeline features."""

    def __init__(self):
        super().__init__({})
        self.calls = 0

    def generate(self, prompt: str, **kwargs) -> str:
        self.calls += 1
        # Extract first absolute line number from code block, if present
        m = re.search(r"```\n(\d+):", prompt)
        line_no = int(m.group(1)) if m else 1
        
        # Check if summary is requested (prompt contains "summary only")
        wants_summary = "summary only" in prompt.lower()
        wants_inline = "inline comments only" in prompt.lower()
        
        # Build JSON response
        if wants_summary:
            # Summary only mode - return object with empty comments and summary
            response = {
                "comments": [],
                "summary": f"Chunk {self.calls} summary."
            }
        elif wants_inline:
            # Inline only mode - return array with comments only
            response = [
                {
                    "file": "example.py",
                    "line": line_no,
                    "message": f"Test comment for chunk {self.calls}.",
                    "suggestion": None
                }
            ]
        else:
            # Both mode - return object with comments and summary
            response = {
                "comments": [
                    {
                        "file": "example.py",
                        "line": line_no,
                        "message": f"Test comment for chunk {self.calls}.",
                        "suggestion": None
                    }
                ],
                "summary": f"Chunk {self.calls} summary."
            }
        
        return json.dumps(response)


def test_review_service_respects_comment_modes():
    provider = DummyCountingProvider()
    fc = FileChange(path="example.py", new_content="print('hi')\nprint('bye')\n")

    rs_summary = ReviewService(provider, comment_mode="summary")
    res_summary = rs_summary.review_file(fc)
    assert res_summary.summary is not None
    assert res_summary.comments == []

    rs_inline = ReviewService(provider, comment_mode="inline")
    res_inline = rs_inline.review_file(fc)
    assert res_inline.summary is None
    assert all(c.is_inline for c in res_inline.comments)
    assert len(res_inline.comments) >= 1


def test_review_service_chunking_triggers_multiple_llm_calls():
    provider = DummyCountingProvider()
    big_lines = [f"line {i} = {i}" for i in range(1, 400)]
    fc = FileChange(path="big.py", new_content="\n".join(big_lines))

    rs = ReviewService(
        provider,
        comment_mode="inline",
        max_context_tokens=200,  # small to force chunking
        chunk_overlap_lines=50,
    )
    res = rs.review_file(fc)

    assert provider.calls > 1
    assert len(res.comments) > 1
    assert all(c.line_number is not None for c in res.comments)

