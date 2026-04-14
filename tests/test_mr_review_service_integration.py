from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional

from luminary.application.mr_review_service import MRReviewService
from luminary.application.review_service import ReviewService
from luminary.domain.models.file_change import FileChange, Hunk
from luminary.domain.models.review_result import ReviewResult
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

    def get_merge_request_changes(
        self, project_id: str, merge_request_iid: int
    ) -> List[FileChange]:
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


class SlowReviewService:
    """Deterministic review service to test ordering with concurrency."""

    def review_file(self, file_change: FileChange):
        # Force out-of-order completion (higher index files finish faster).
        if file_change.path.endswith("1.py"):
            time.sleep(0.03)
        elif file_change.path.endswith("2.py"):
            time.sleep(0.02)
        else:
            time.sleep(0.01)
        return ReviewResult(file_change=file_change, comments=[], summary=f"summary for {file_change.path}")


def test_mr_review_service_respects_comment_mode_summary_only():
    fc = FileChange(
        path="a.py", new_content="print('x')\n", hunks=[Hunk(1, 1, 1, 1, ["+print('x')"])]
    )
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


def test_parallel_review_preserves_input_order():
    files = [
        FileChange(path="f1.py", new_content="print('1')\n"),
        FileChange(path="f2.py", new_content="print('2')\n"),
        FileChange(path="f3.py", new_content="print('3')\n"),
    ]
    fake_gitlab = FakeGitLabClient(files)
    provider = DummyProvider()

    mr = MRReviewService(
        llm_provider=provider,
        gitlab_client=fake_gitlab,  # type: ignore[arg-type]
        file_filter=FileFilter(),
        review_service=SlowReviewService(),  # type: ignore[arg-type]
        max_concurrent_files=3,
        comment_mode="both",
    )

    review_items = mr._run_file_reviews(files)
    assert [idx for idx, *_ in review_items] == [1, 2, 3]
    assert [item[1].path for item in review_items] == ["f1.py", "f2.py", "f3.py"]


def test_mr_review_service_exposes_observability_stats():
    files = [
        FileChange(path="f1.py", new_content="print('1')\n"),
        FileChange(path="f2.py", new_content="print('2')\n"),
    ]
    fake_gitlab = FakeGitLabClient(files)
    provider = DummyProvider()

    mr = MRReviewService(
        llm_provider=provider,
        gitlab_client=fake_gitlab,  # type: ignore[arg-type]
        file_filter=FileFilter(),
        review_service=SlowReviewService(),  # type: ignore[arg-type]
        max_concurrent_files=2,
        comment_mode="summary",
    )

    stats = mr.review_merge_request("group/proj", 1, post_comments=False)
    assert "post_success_rate" in stats
    assert "llm_fallback_count" in stats
    assert "review_duration_ms_total" in stats
    assert "review_duration_ms_avg" in stats
