"""Service for reviewing merge requests"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

from luminary.application.review_service import ReviewService
from luminary.domain.models.file_change import FileChange
from luminary.domain.models.review_result import ReviewResult
from luminary.infrastructure.file_filter import FileFilter
from luminary.infrastructure.gitlab.client import GitLabClient
from luminary.infrastructure.llm.base import LLMProvider

logger = logging.getLogger(__name__)


class MRReviewService:
    """Service for reviewing entire merge requests"""

    llm_provider: LLMProvider
    gitlab_client: GitLabClient
    file_filter: FileFilter
    review_service: ReviewService
    max_files: Optional[int]
    max_lines: Optional[int]
    max_concurrent_files: int
    comment_mode: str

    def __init__(
        self,
        llm_provider: LLMProvider,
        gitlab_client: GitLabClient,
        file_filter: Optional[FileFilter] = None,
        review_service: Optional[ReviewService] = None,
        max_files: Optional[int] = None,
        max_lines: Optional[int] = None,
        max_concurrent_files: int = 1,
        comment_mode: str = "both",
    ):
        """Initialize MR review service

        Args:
            llm_provider: LLM provider for code review
            gitlab_client: GitLab API client
            file_filter: File filter (uses default if None)
            review_service: Review service (creates default if None)
            max_files: Maximum number of files to process (None = no limit)
            max_lines: Maximum number of lines to process (None = no limit)
        """
        self.llm_provider = llm_provider
        self.gitlab_client = gitlab_client
        self.file_filter = file_filter or FileFilter()
        self.review_service = review_service or ReviewService(llm_provider)
        self.max_files = max_files
        self.max_lines = max_lines
        self.max_concurrent_files = max(1, max_concurrent_files)
        self.comment_mode = comment_mode

    def review_merge_request(
        self, project_id: str, merge_request_iid: int, post_comments: bool = True
    ) -> dict:
        """Review entire merge request

        Args:
            project_id: GitLab project ID or path
            merge_request_iid: Merge request IID
            post_comments: Whether to post comments to GitLab

        Returns:
            Dictionary with review statistics
        """
        logger.info(f"Starting review of MR !{merge_request_iid} in {project_id}")

        # Get file changes from GitLab
        file_changes = self.gitlab_client.get_merge_request_changes(project_id, merge_request_iid)

        # Filter files
        filtered_files, ignored_files = self.file_filter.filter_files(file_changes)

        # Check limits
        if self.max_files and len(filtered_files) > self.max_files:
            logger.warning(
                f"MR has {len(filtered_files)} files, limit is {self.max_files}. "
                f"Processing first {self.max_files} files."
            )
            filtered_files = filtered_files[: self.max_files]

        # Check line limit
        total_lines = sum(fc.total_lines_changed for fc in filtered_files)
        if self.max_lines and total_lines > self.max_lines:
            logger.warning(
                f"MR has {total_lines} changed lines, limit is {self.max_lines}. "
                f"Processing files until limit is reached."
            )
            # Process files until line limit
            processed_files = []
            lines_processed = 0
            for fc in filtered_files:
                if lines_processed + fc.total_lines_changed > self.max_lines:
                    break
                processed_files.append(fc)
                lines_processed += fc.total_lines_changed
            filtered_files = processed_files

        # Process files (optionally in parallel for LLM-bound work).
        # Posting to GitLab remains ordered and sequential.
        review_items = self._run_file_reviews(filtered_files)

        results = []
        comments_posted = 0
        comments_failed = 0
        llm_fallback_count = 0
        total_review_duration_ms = 0

        for _, file_change, result, duration_ms in review_items:
            total_review_duration_ms += duration_ms

            if result.error:
                logger.error(f"Error reviewing {file_change.path}: {result.error}")
                continue

            results.append(result)
            llm_fallback_count += self._count_fallback_comments(result)

            # Post comments to GitLab
            # Pass original file_change to preserve full file content for line_code calculation
            if post_comments and result.has_comments and self.comment_mode in ("inline", "both"):
                posted, failed = self._post_comments_to_gitlab(
                    project_id, merge_request_iid, result, file_change
                )
                comments_posted += posted
                comments_failed += failed

        # Post summary comment if there are results and mode allows it
        summary_attempted = 0
        summary_posted = False
        if post_comments and results and self.comment_mode in ("summary", "both"):
            summary_attempted = 1
            summary_posted = self._post_summary_comment(project_id, merge_request_iid, results)
        summary_failed = 1 if summary_attempted and not summary_posted else 0

        total_posted = comments_posted + (1 if summary_posted else 0)
        total_failed = comments_failed + summary_failed
        total_attempted = total_posted + total_failed
        post_success_rate = (total_posted / total_attempted) if total_attempted else 1.0

        # Statistics
        stats = {
            "total_files": len(file_changes),
            "filtered_files": len(filtered_files),
            "ignored_files": len(ignored_files),
            "processed_files": len(results),
            "total_comments": sum(len(r.comments) for r in results),
            "comments_posted": total_posted,
            "comments_failed": total_failed,
            "post_success_rate": round(post_success_rate, 4),
            "llm_fallback_count": llm_fallback_count,
            "review_duration_ms_total": total_review_duration_ms,
            "review_duration_ms_avg": (
                round(total_review_duration_ms / len(review_items), 2) if review_items else 0.0
            ),
        }

        logger.info(f"Review completed. Stats: {stats}")
        return stats

    def _run_file_reviews(self, filtered_files: List[FileChange]) -> List[tuple[int, FileChange, ReviewResult, int]]:
        """Run file reviews sequentially or in parallel.

        Returns:
            List of tuples: (index, file_change, review_result, duration_ms)
        """
        if not filtered_files:
            return []

        if self.max_concurrent_files <= 1 or len(filtered_files) == 1:
            return [
                self._review_single_file(i, len(filtered_files), fc)
                for i, fc in enumerate(filtered_files, 1)
            ]

        max_workers = min(self.max_concurrent_files, len(filtered_files))
        logger.info(
            f"Running parallel review for {len(filtered_files)} files "
            f"with max_workers={max_workers}"
        )

        items: List[tuple[int, FileChange, ReviewResult, int]] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(self._review_single_file, i, len(filtered_files), fc): i
                for i, fc in enumerate(filtered_files, 1)
            }
            for future in as_completed(future_map):
                items.append(future.result())

        return sorted(items, key=lambda item: item[0])

    def _review_single_file(
        self, index: int, total_files: int, file_change: FileChange
    ) -> tuple[int, FileChange, ReviewResult, int]:
        """Review one file and return metadata for deterministic merge."""
        logger.info(f"Processing file {index}/{total_files}: {file_change.path}")
        started = time.monotonic()

        try:
            result = self.review_service.review_file(file_change)
        except Exception as e:
            logger.error(f"Error processing file {file_change.path}: {e}", exc_info=True)
            result = ReviewResult(file_change=file_change, error=str(e))

        duration_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "File review finished",
            extra={
                "component": "mr_review_service",
                "operation": "review_file",
                "file_path": file_change.path,
                "duration_ms": duration_ms,
            },
        )
        return (index, file_change, result, duration_ms)

    def _count_fallback_comments(self, result: ReviewResult) -> int:
        """Count fallback parser comments for observability metrics."""
        fallback_markers = ("[Parsing error", "[Error parsing response]")
        return sum(1 for c in result.comments if any(marker in c.content for marker in fallback_markers))

    def _post_comments_to_gitlab(
        self,
        project_id: str,
        merge_request_iid: int,
        result: ReviewResult,
        original_file_change: FileChange,
    ) -> tuple[int, int]:
        """Post comments from review result to GitLab

        Args:
            project_id: Project ID
            merge_request_iid: Merge request IID
            result: Review result
            original_file_change: Original FileChange (preserves full file content)

        Returns:
            Tuple of (posted_count, failed_count)
        """
        posted = 0
        failed = 0

        # Only post inline comments here; summary is posted separately.
        # Use original file_change content for line_code calculation (preserves full file content)
        # When chunking is used, result.file_change.new_content contains only the last chunk
        file_content = original_file_change.new_content if original_file_change else None

        # Log for debugging
        if not file_content:
            logger.warning(
                f"original_file_change.new_content is None for {result.file_change.path}. "
                "line_code calculation will fall back to API."
            )
        else:
            logger.debug(
                f"Using original file content for line_code calculation: "
                f"{result.file_change.path} ({len(file_content)} chars, "
                f"{len(file_content.split(chr(10)))} lines)"
            )

        # Debug: log all comments to understand what we have
        all_comments_count = len(result.comments)
        inline_comments_count = len(result.inline_comments)
        logger.debug(
            f"Posting comments for {result.file_change.path}: "
            f"{all_comments_count} total comments, {inline_comments_count} inline comments. "
            f"Comment details: {[(c.line_number, len(c.content)) for c in result.comments]}"
        )

        for comment in result.inline_comments:
            try:
                success = self.gitlab_client.post_comment(
                    project_id=project_id,
                    merge_request_iid=merge_request_iid,
                    body=comment.to_markdown(),
                    line_number=comment.line_number,
                    file_path=comment.file_path or result.file_change.path,
                    line_type=comment.line_type,
                    file_content=file_content,
                )
                if success:
                    posted += 1
                else:
                    failed += 1
            except Exception as e:
                logger.error(f"Failed to post comment: {e}")
                failed += 1

        return posted, failed

    def _post_summary_comment(
        self, project_id: str, merge_request_iid: int, results: List[ReviewResult]
    ) -> bool:
        """Post summary comment to merge request

        Args:
            project_id: Project ID
            merge_request_iid: Merge request IID
            results: List of review results

        Returns:
            True if summary was posted successfully
        """
        # Build summary
        total_comments = sum(len(r.comments) for r in results)
        files_with_issues = sum(1 for r in results if r.has_comments)

        summary_lines = [
            "## Code Review Summary",
            "",
            f"**Files reviewed:** {len(results)}",
            f"**Files with issues:** {files_with_issues}",
            f"**Total comments:** {total_comments}",
            "",
        ]

        # Add summaries from individual files
        for result in results:
            if result.summary:
                summary_lines.append(f"### {result.file_change.path}")
                summary_lines.append(result.summary)
                summary_lines.append("")

        summary_body = "\n".join(summary_lines)

        try:
            success = self.gitlab_client.post_comment(
                project_id=project_id,
                merge_request_iid=merge_request_iid,
                body=summary_body,
            )
            if success:
                logger.info("Posted summary comment to MR")
            return success
        except Exception as e:
            logger.error(f"Failed to post summary comment: {e}")
            return False
