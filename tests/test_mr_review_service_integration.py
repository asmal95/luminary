from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from luminary.application.mr_review_service import MRReviewService
from luminary.application.review_service import ReviewService
from luminary.domain.models.file_change import FileChange, Hunk
from luminary.infrastructure.file_filter import FileFilter
from luminary.infrastructure.llm.base import LLMProvider


class DummyProvider(LLMProvider):
    def __init__(self):
        super().__init__({})

    def generate(self, prompt: str, **kwargs) -> str:
        return "**Line 1:** [INFO] Dummy\n\n**Summary:** Dummy summary"


@dataclass
class Posted:
    body: str
    line_number: Optional[int]
    file_path: Optional[str]


class FakeGitLabClient:
    def __init__(self, file_changes: List[FileChange]):
        self._file_changes = file_changes
        self.posted: List[Posted] = []

    def get_merge_request_changes(self, project_id: str, merge_request_iid: int) -> List[FileChange]:
        return list(self._file_changes)

    def post_comment(
        self,
        project_id: str,
        merge_request_iid: int,
        body: str,
        line_number: Optional[int] = None,
        file_path: Optional[str] = None,
        line_type: str = "new",
    ) -> bool:
        self.posted.append(Posted(body=body, line_number=line_number, file_path=file_path))
        return True


def test_mr_review_service_respects_comment_mode_summary_only():
    fc = FileChange(path="a.py", new_content="print('x')\n", hunks=[Hunk(1, 1, 1, 1, ["+print('x')"])])
    fake_gitlab = FakeGitLabClient([fc])

    provider = DummyProvider()
    review_service = ReviewService(provider, comment_mode="both")

    mr = MRReviewService(
        llm_provider=provider,
        gitlab_client=fake_gitlab,  # type: ignore[arg-type]
        file_filter=FileFilter(),
        review_service=review_service,
        comment_mode="summary",
    )

    stats = mr.review_merge_request("group/proj", 1, post_comments=True)
    assert stats["processed_files"] == 1
    # Summary should be posted, but no inline comments
    assert any(p.line_number is None for p in fake_gitlab.posted)
    assert not any(p.line_number is not None for p in fake_gitlab.posted)

