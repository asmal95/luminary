"""GitLab API client"""

import logging
import os
import time
from typing import Dict, List, Optional, TYPE_CHECKING

import gitlab
from gitlab.exceptions import GitlabError

from luminary.domain.models.file_change import FileChange, Hunk
from luminary.infrastructure.diff_parser import parse_unified_diff

if TYPE_CHECKING:
    from gitlab.v4.objects import ProjectMergeRequest

logger = logging.getLogger(__name__)


class GitLabClient:
    """Client for GitLab API operations"""

    def __init__(
        self,
        gitlab_url: Optional[str] = None,
        private_token: Optional[str] = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ):
        """Initialize GitLab client
        
        Args:
            gitlab_url: GitLab instance URL (default: from GITLAB_URL env or gitlab.com)
            private_token: GitLab private token (default: from GITLAB_TOKEN env)
            max_retries: Maximum retry attempts for API calls
            retry_delay: Initial retry delay in seconds
        """
        self.gitlab_url = gitlab_url or os.getenv("GITLAB_URL", "https://gitlab.com")
        self.private_token = private_token or os.getenv("GITLAB_TOKEN")
        self.max_retries = max_retries
        self.retry_delay = retry_delay

        if not self.private_token:
            raise ValueError(
                "GitLab private token is required. "
                "Set GITLAB_TOKEN environment variable or provide in config."
            )

        # Initialize GitLab connection
        self.gl = gitlab.Gitlab(self.gitlab_url, private_token=self.private_token)
        self.gl.auth()  # Verify authentication

        logger.info(f"GitLab client initialized for {self.gitlab_url}")

    def get_merge_request(
        self, project_id: str, merge_request_iid: int
    ) -> "ProjectMergeRequest":
        """Get merge request by project ID and MR IID
        
        Args:
            project_id: Project ID or path (e.g., "group/project")
            merge_request_iid: Merge request IID (internal ID)
            
        Returns:
            Merge request object
            
        Raises:
            RuntimeError: If MR cannot be retrieved
        """
        return self._retry_api_call(
            lambda: self.gl.projects.get(project_id).mergerequests.get(merge_request_iid)
        )

    def get_merge_request_changes(
        self, project_id: str, merge_request_iid: int
    ) -> List[FileChange]:
        """Get file changes from merge request
        
        Args:
            project_id: Project ID or path
            merge_request_iid: Merge request IID
            
        Returns:
            List of FileChange objects
        """
        logger.info(f"Fetching changes for MR !{merge_request_iid} in {project_id}")

        mr = self.get_merge_request(project_id, merge_request_iid)

        # Get MR diff
        changes = self._retry_api_call(lambda: mr.changes())

        file_changes = []

        for change in changes.get("changes", []):
            try:
                file_change = self._parse_gitlab_change(change, project_id, mr)
                if file_change:
                    file_changes.append(file_change)
            except Exception as e:
                logger.warning(f"Failed to parse change for {change.get('old_path', 'unknown')}: {e}")
                continue

        logger.info(f"Parsed {len(file_changes)} file changes from MR")
        return file_changes

    def _parse_gitlab_change(
        self, change: Dict, project_id: str, mr: "ProjectMergeRequest"
    ) -> Optional[FileChange]:
        """Parse GitLab change into FileChange object
        
        Args:
            change: Change dictionary from GitLab API
            project_id: Project ID
            mr: Merge request object
            
        Returns:
            FileChange object or None if parsing fails
        """
        old_path = change.get("old_path")
        new_path = change.get("new_path")
        diff = change.get("diff", "")

        if not new_path and not old_path:
            return None

        # Determine status
        if not old_path:
            status = "added"
        elif not new_path:
            status = "deleted"
        elif old_path != new_path:
            status = "renamed"
        else:
            status = "modified"

        # Parse diff into hunks
        hunks = self._parse_diff_to_hunks(diff)

        # Get file content if available (for new/modified files)
        new_content = None
        if new_path and status != "deleted":
            try:
                # Get project separately (mr.project may not be available in some GitLab versions)
                project = self._retry_api_call(lambda: self.gl.projects.get(project_id))
                # Try to get file content from MR branch
                file_content = self._retry_api_call(
                    lambda: project.files.get(new_path, ref=mr.source_branch).decode()
                )
                new_content = file_content
            except Exception as e:
                logger.debug(f"Could not fetch content for {new_path}: {e}")

        return FileChange(
            path=new_path or old_path,
            old_path=old_path if old_path != new_path else None,
            status=status,
            hunks=hunks,
            new_content=new_content,
        )

    def _parse_diff_to_hunks(self, diff: str) -> List[Hunk]:
        """Parse unified diff string into Hunk objects
        
        Args:
            diff: Unified diff string
            
        Returns:
            List of Hunk objects
        """
        if not diff:
            return []

        hunks = []
        lines = diff.split("\n")

        current_hunk = None
        hunk_lines = []

        for line in lines:
            # Parse hunk header: @@ -old_start,old_count +new_start,new_count @@
            if line.startswith("@@ "):
                # Save previous hunk if exists
                if current_hunk:
                    current_hunk.lines = hunk_lines
                    hunks.append(current_hunk)

                # Parse hunk header
                import re

                match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
                if match:
                    old_start = int(match.group(1))
                    old_count = int(match.group(2)) if match.group(2) else 1
                    new_start = int(match.group(3))
                    new_count = int(match.group(4)) if match.group(4) else 1

                    current_hunk = Hunk(
                        old_start=old_start,
                        old_count=old_count,
                        new_start=new_start,
                        new_count=new_count,
                        lines=[],
                    )
                    hunk_lines = []

            # Parse hunk lines
            elif current_hunk and (
                line.startswith(" ") or line.startswith("-") or line.startswith("+")
            ):
                hunk_lines.append(line)

        # Save last hunk
        if current_hunk:
            current_hunk.lines = hunk_lines
            hunks.append(current_hunk)

        return hunks

    def post_comment(
        self,
        project_id: str,
        merge_request_iid: int,
        body: str,
        line_number: Optional[int] = None,
        file_path: Optional[str] = None,
        line_type: str = "new",
    ) -> bool:
        """Post a comment to merge request
        
        Args:
            project_id: Project ID or path
            merge_request_iid: Merge request IID
            body: Comment body (markdown supported)
            line_number: Line number for inline comment (None for general comment)
            file_path: File path for inline comment
            line_type: Line type ("new" or "old") for inline comment
            
        Returns:
            True if comment was posted successfully
        """
        try:
            mr = self.get_merge_request(project_id, merge_request_iid)

            if line_number and file_path:
                # Inline comment
                self._retry_api_call(
                    lambda: mr.discussions.create(
                        {
                            "body": body,
                            "position": {
                                "base_sha": mr.diff_refs["base_sha"],
                                "start_sha": mr.diff_refs["start_sha"],
                                "head_sha": mr.diff_refs["head_sha"],
                                "old_path": file_path,
                                "new_path": file_path,
                                "position_type": "text",
                                "new_line": line_number if line_type == "new" else None,
                                "old_line": line_number if line_type == "old" else None,
                            },
                        }
                    )
                )
                logger.debug(f"Posted inline comment to {file_path}:{line_number}")
            else:
                # General comment
                self._retry_api_call(lambda: mr.notes.create({"body": body}))
                logger.debug("Posted general comment to MR")

            return True

        except Exception as e:
            logger.error(f"Failed to post comment: {e}", exc_info=True)
            return False

    def _retry_api_call(self, func, *args, **kwargs):
        """Execute API call with retry logic
        
        Args:
            func: Function to execute
            *args: Function arguments
            **kwargs: Function keyword arguments
            
        Returns:
            Function result
            
        Raises:
            RuntimeError: If all retries failed
        """
        last_error = None

        for attempt in range(self.max_retries):
            try:
                return func(*args, **kwargs)
            except GitlabError as e:
                last_error = e
                status_code = e.response_code if hasattr(e, "response_code") else None

                # Don't retry on auth errors
                if status_code in (401, 403):
                    logger.error(f"GitLab API authentication error: {e}")
                    raise RuntimeError(f"GitLab API authentication failed: {e}") from e

                # Don't retry on client errors (except rate limits)
                if status_code and 400 <= status_code < 500 and status_code != 429:
                    logger.error(f"GitLab API client error: {e}")
                    raise RuntimeError(f"GitLab API client error: {e}") from e

                # Retry on rate limits and server errors
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2 ** attempt)
                    logger.warning(
                        f"GitLab API error (attempt {attempt + 1}/{self.max_retries}): {e}. "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        f"GitLab API failed after {self.max_retries} attempts: {e}"
                    )
                    raise RuntimeError(
                        f"GitLab API request failed after {self.max_retries} attempts: {e}"
                    ) from e

            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2 ** attempt)
                    logger.warning(
                        f"GitLab API error (attempt {attempt + 1}/{self.max_retries}): {e}. "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        f"GitLab API error after {self.max_retries} attempts: {e}"
                    )
                    raise RuntimeError(
                        f"GitLab API error after {self.max_retries} attempts: {e}"
                    ) from e

        raise RuntimeError(f"GitLab API request failed: {last_error}") from last_error
