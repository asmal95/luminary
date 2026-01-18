"""Service for reviewing merge requests"""

import logging
from typing import List, Optional

from luminary.application.review_service import ReviewService
from luminary.domain.models.comment import Comment
from luminary.domain.models.file_change import FileChange
from luminary.domain.models.review_result import ReviewResult
from luminary.infrastructure.file_filter import FileFilter
from luminary.infrastructure.gitlab.client import GitLabClient
from luminary.infrastructure.llm.base import LLMProvider

logger = logging.getLogger(__name__)


class MRReviewService:
    """Service for reviewing entire merge requests"""

    def __init__(
        self,
        llm_provider: LLMProvider,
        gitlab_client: GitLabClient,
        file_filter: Optional[FileFilter] = None,
        review_service: Optional[ReviewService] = None,
        max_files: Optional[int] = None,
        max_lines: Optional[int] = None,
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
        file_changes = self.gitlab_client.get_merge_request_changes(
            project_id, merge_request_iid
        )

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

        # Process files sequentially
        results = []
        comments_posted = 0
        comments_failed = 0

        for i, file_change in enumerate(filtered_files, 1):
            logger.info(
                f"Processing file {i}/{len(filtered_files)}: {file_change.path}"
            )

            try:
                # Review file
                result = self.review_service.review_file(file_change)

                if result.error:
                    logger.error(f"Error reviewing {file_change.path}: {result.error}")
                    continue

                results.append(result)

                # Post comments to GitLab
                # Pass original file_change to preserve full file content for line_code calculation
                if post_comments and result.has_comments and self.comment_mode in ("inline", "both"):
                    posted, failed = self._post_comments_to_gitlab(
                        project_id, merge_request_iid, result, file_change
                    )
                    comments_posted += posted
                    comments_failed += failed

            except Exception as e:
                logger.error(
                    f"Error processing file {file_change.path}: {e}", exc_info=True
                )
                continue

        # Post summary comment if there are results and mode allows it
        summary_posted = False
        if post_comments and results and self.comment_mode in ("summary", "both"):
            summary_posted = self._post_summary_comment(project_id, merge_request_iid, results)

        # Statistics
        stats = {
            "total_files": len(file_changes),
            "filtered_files": len(filtered_files),
            "ignored_files": len(ignored_files),
            "processed_files": len(results),
            "total_comments": sum(len(r.comments) for r in results),
            "comments_posted": comments_posted + (1 if summary_posted else 0),
            "comments_failed": comments_failed,
        }

        logger.info(f"Review completed. Stats: {stats}")
        return stats

    def _post_comments_to_gitlab(
        self, project_id: str, merge_request_iid: int, result: ReviewResult, original_file_change: FileChange
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