"""GitLab API client"""

import base64
import hashlib
import json
import logging
import os
import re
from typing import Dict, List, Optional, TYPE_CHECKING, Any

import gitlab
from gitlab.exceptions import GitlabError

from luminary.domain.models.file_change import FileChange, Hunk
from luminary.infrastructure.http_client import RetryConfig, retry_config_from_dict
from luminary.infrastructure.retry import _should_retry_gitlab_error

if TYPE_CHECKING:
    from gitlab.v4.objects import ProjectMergeRequest

logger = logging.getLogger(__name__)


class GitLabClient:
    """Client for GitLab API operations"""

    def __init__(
        self,
        gitlab_url: Optional[str] = None,
        private_token: Optional[str] = None,
        retry_config: Optional[RetryConfig] = None,
        # Legacy parameters for backward compatibility
        max_retries: Optional[int] = None,
        retry_delay: Optional[float] = None,
    ):
        """Initialize GitLab client
        
        Args:
            gitlab_url: GitLab instance URL (default: from GITLAB_URL env or gitlab.com)
            private_token: GitLab private token (default: from GITLAB_TOKEN env)
            retry_config: Retry configuration (takes precedence over max_retries/retry_delay)
            max_retries: Maximum retry attempts for API calls (legacy, use retry_config)
            retry_delay: Initial retry delay in seconds (legacy, use retry_config)
        """
        self.gitlab_url = gitlab_url or os.getenv("GITLAB_URL", "https://gitlab.com")
        self.private_token = private_token or os.getenv("GITLAB_TOKEN")

        # Handle retry config - support both new and legacy parameters
        if retry_config is not None:
            self.retry_config = retry_config
        elif max_retries is not None or retry_delay is not None:
            # Legacy support: create RetryConfig from old parameters
            config_dict = {}
            if max_retries is not None:
                config_dict["max_attempts"] = max_retries
            if retry_delay is not None:
                config_dict["initial_delay"] = retry_delay
            self.retry_config = retry_config_from_dict(config_dict)
        else:
            # Default retry config
            self.retry_config = RetryConfig()

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

    def _decode_file_object(self, file_obj: Any, file_path: str) -> Optional[str]:
        """Decode file object from GitLab API to string content
        
        Handles different return types from python-gitlab library:
        - bytes: direct decode
        - decode_bytes() method: returns bytes, then decode to string
        - content attribute: Base64-encoded string
        - decode() method: last resort
        - data attribute: fallback
        - str conversion: last resort
        
        Args:
            file_obj: File object from GitLab API
            file_path: File path (for logging)
            
        Returns:
            Decoded file content as string, or None if decoding fails
        """
        if isinstance(file_obj, bytes):
            return file_obj.decode('utf-8')
        
        if hasattr(file_obj, 'decode_bytes'):
            try:
                decoded_bytes = file_obj.decode_bytes()
                return decoded_bytes.decode('utf-8')
            except Exception as e:
                logger.warning(f"decode_bytes() failed for {file_path}: {e}")
                return None
        
        if hasattr(file_obj, 'content'):
            file_content = file_obj.content
            if isinstance(file_content, bytes):
                # Try Base64 decode first, fallback to direct decode
                try:
                    return base64.b64decode(file_content).decode('utf-8')
                except Exception:
                    return file_content.decode('utf-8')
            elif isinstance(file_content, str):
                # ProjectFile.content is Base64-encoded string
                try:
                    return base64.b64decode(file_content).decode('utf-8')
                except Exception:
                    # Fallback: use as-is (shouldn't happen)
                    return file_content
            else:
                return str(file_content) if file_content else None
        
        if hasattr(file_obj, 'decode'):
            try:
                decoded = file_obj.decode()
                if isinstance(decoded, bytes):
                    return decoded.decode('utf-8')
                elif isinstance(decoded, str):
                    # Try Base64 decode if it looks like Base64
                    try:
                        return base64.b64decode(decoded).decode('utf-8')
                    except Exception:
                        return decoded
                else:
                    return str(decoded)
            except Exception as e:
                logger.warning(f"decode() failed for {file_path}: {e}")
                return None
        
        if hasattr(file_obj, 'data'):
            file_data = file_obj.data
            if isinstance(file_data, bytes):
                return file_data.decode('utf-8')
            return str(file_data)
        
        # Last resort: convert to string
        return str(file_obj) if file_obj else None

    def _get_file_content_via_repository_blob(
        self, project: Any, file_path: str, ref: str
    ) -> Optional[str]:
        """Get file content via repository_blob API
        
        Uses retry for transient errors (500, 429), but NOT for 404 (file not found).
        404 is a normal response indicating the file doesn't exist in that ref.
        
        Args:
            project: GitLab project object
            file_path: File path
            ref: Git reference (branch or commit SHA)
            
        Returns:
            File content as string, or None if not available
        """
        if not hasattr(project, 'repository_blob'):
            return None
        
        # Try direct call first to catch 404 early (no retry needed for 404)
        try:
            blob = project.repository_blob(file_path, ref=ref)
            if blob:
                if isinstance(blob, bytes):
                    return blob.decode('utf-8')
                return str(blob)
        except GitlabError as e:
            status_code = getattr(e, 'response_code', None)
            if status_code == 404:
                # 404 is normal - file may not exist in this ref, no retry needed
                logger.debug(f"File {file_path} not found in ref {ref} via repository_blob")
                return None
            # For other errors, use retry logic
            try:
                # Retry for transient errors (500, 429, network issues)
                blob = self._retry_api_call(lambda: project.repository_blob(file_path, ref=ref))
                if blob:
                    if isinstance(blob, bytes):
                        return blob.decode('utf-8')
                    return str(blob)
            except RuntimeError:
                # Retry exhausted or non-retryable error (401, 403, etc.)
                logger.debug(f"repository_blob failed for {file_path}@{ref} after retries: {e}")
        except (AttributeError, Exception) as e:
            logger.debug(f"repository_blob exception for {file_path}@{ref}: {e}")
        
        return None

    def _get_file_content_via_files_get(
        self, project: Any, file_path: str, ref: str
    ) -> Optional[str]:
        """Get file content via files.get API
        
        Args:
            project: GitLab project object
            file_path: File path
            ref: Git reference (branch or commit SHA)
            
        Returns:
            File content as string, or None if not available
        """
        try:
            file_obj = self._retry_api_call(lambda: project.files.get(file_path, ref=ref))
            content = self._decode_file_object(file_obj, file_path)
            
            if content and content.strip():
                logger.debug(f"Successfully fetched content via files.get for {file_path} ({len(content)} chars)")
                return content
            else:
                logger.warning(f"files.get returned empty content for {file_path}")
        except Exception as e:
            logger.warning(f"Could not fetch content via files.get for {file_path}: {e}", exc_info=True)
        
        return None

    def _get_file_content(
        self, project_id: str, file_path: str, mr: "ProjectMergeRequest"
    ) -> Optional[str]:
        """Get file content using multiple strategies
        
        Tries repository_blob first, then falls back to files.get.
        Optimized to avoid unnecessary API calls when repository_blob doesn't work.
        
        Args:
            project_id: Project ID or path
            file_path: File path
            mr: Merge request object
            
        Returns:
            File content as string, or None if not available
        """
        try:
            project = self._retry_api_call(lambda: self.gl.projects.get(project_id))
            
            # Try repository_blob with source_branch first (most common case)
            if mr.source_branch:
                content = self._get_file_content_via_repository_blob(project, file_path, mr.source_branch)
                if content:
                    logger.debug(f"Successfully fetched content via repository_blob for {file_path} ({len(content)} chars)")
                    return content
            
            # Fallback to files.get (more reliable, works even if repository_blob doesn't)
            if mr.source_branch:
                content = self._get_file_content_via_files_get(project, file_path, mr.source_branch)
                if content:
                    return content
            
            # Last resort: try repository_blob with head_sha if different from source_branch
            head_sha = mr.diff_refs.get("head_sha")
            if head_sha and head_sha != mr.source_branch:
                content = self._get_file_content_via_repository_blob(project, file_path, head_sha)
                if content:
                    logger.debug(f"Successfully fetched content via repository_blob (head_sha) for {file_path} ({len(content)} chars)")
                    return content
                
                # Try files.get with head_sha as last resort
                content = self._get_file_content_via_files_get(project, file_path, head_sha)
                if content:
                    return content
        except Exception as e:
            logger.warning(f"Could not fetch content for {file_path}: {e}")
        
        return None

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
            new_content = self._get_file_content(project_id, new_path, mr)

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

    def _calculate_line_code(self, project_id: str, file_path: str, line_number: int, mr: "ProjectMergeRequest") -> Optional[str]:
        """Calculate line_code for GitLab inline comment
        
        Args:
            project_id: Project ID or path
            file_path: File path
            line_number: Line number (1-based)
            mr: Merge request object (to get refs)
            
        Returns:
            line_code hash or None if cannot calculate
        """
        try:
            project = self._retry_api_call(lambda: self.gl.projects.get(project_id))
            
            refs_to_try = [
                mr.source_branch,
                mr.diff_refs.get("head_sha"),
            ]
            
            content = None
            for ref in refs_to_try:
                if not ref:
                    continue
                try:
                    # files.get is called directly (not through retry) in _calculate_line_code
                    file_obj = project.files.get(file_path, ref=ref)
                    content = self._decode_file_object(file_obj, file_path)
                    if content:
                        break
                except GitlabError as e:
                    status_code = getattr(e, "response_code", None) if hasattr(e, "response_code") else None
                    if status_code == 404:
                        logger.debug(f"File {file_path} not found in ref {ref} (may be new file)")
                    else:
                        logger.debug(f"Could not get file {file_path} from ref {ref}: {e}")
                    continue
                except Exception as e:
                    logger.debug(f"Could not get file {file_path} from ref {ref}: {e}")
                    continue
            
            if not content:
                logger.debug(f"Empty content for {file_path} after trying all refs")
                return None
            
            # Validate line number
            lines = content.splitlines()
            if not (1 <= line_number <= len(lines)):
                logger.debug(f"Line {line_number} out of range for {file_path} (file has {len(lines)} lines)")
                return None
            
            # GitLab line_code format: <SHA-1 of file path>_<old_line>_<new_line>
            file_sha1 = hashlib.sha1(file_path.encode('utf-8')).hexdigest()
            line_code = f"{file_sha1}_{line_number}_{line_number}"
            return line_code
        except Exception as e:
            logger.debug(f"Could not calculate line_code for {file_path}:{line_number}: {e}")
        return None

    def _calculate_line_code_from_content(
        self, file_path: str, line_number: int, file_content: str
    ) -> Optional[str]:
        """Calculate line_code from provided file content
        
        Args:
            file_path: File path
            line_number: Line number (1-based)
            file_content: File content (may be Base64-encoded)
            
        Returns:
            line_code hash or None if cannot calculate
        """
        try:
            # Try to decode Base64 if it looks like Base64
            decoded_content = self._maybe_decode_base64(file_content, file_path)
            
            lines = decoded_content.splitlines()
            if not (1 <= line_number <= len(lines)):
                preview = decoded_content[:200].replace('\n', '\\n').replace('\r', '\\r')
                logger.warning(
                    f"Line {line_number} out of range for {file_path} "
                    f"(file has {len(lines)} lines, content length: {len(decoded_content)} chars). "
                    f"Content preview: {preview[:100]}..."
                )
                return None
            
            # GitLab line_code format: <SHA-1 of file path>_<old_line>_<new_line>
            file_sha1 = hashlib.sha1(file_path.encode('utf-8')).hexdigest()
            line_code = f"{file_sha1}_{line_number}_{line_number}"
            logger.debug(f"Successfully calculated line_code from file_content: {line_code}")
            return line_code
        except Exception as e:
            logger.warning(f"Could not calculate line_code from provided content: {e}", exc_info=True)
        return None

    def _maybe_decode_base64(self, content: str, file_path: str) -> str:
        """Try to decode Base64 content if it looks like Base64
        
        Args:
            content: Content string (may be Base64-encoded)
            file_path: File path (for logging)
            
        Returns:
            Decoded content (or original if not Base64)
        """
        if not isinstance(content, str) or len(content) <= 50:
            return content
        
        # Check if it looks like Base64 (only base64 chars, no newlines for short strings)
        is_likely_base64 = (
            all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n' for c in content[:100])
            and '\n' not in content[:200]  # Base64 usually has no newlines in content
        )
        
        if is_likely_base64:
            try:
                decoded_bytes = base64.b64decode(content)
                decoded_content = decoded_bytes.decode('utf-8')
                logger.debug(f"Decoded Base64 content for {file_path} ({len(decoded_content)} chars after decode)")
                return decoded_content
            except Exception as e:
                logger.debug(f"Content doesn't seem to be Base64: {e}, using as-is")
        
        return content

    def _post_inline_comment(
        self,
        mr: "ProjectMergeRequest",
        project_id: str,
        body: str,
        file_path: str,
        line_number: int,
        line_type: str,
        file_content: Optional[str] = None,
    ) -> bool:
        """Post inline comment to merge request
        
        Args:
            mr: Merge request object
            project_id: Project ID or path
            body: Comment body
            file_path: File path
            line_number: Line number
            line_type: Line type ("new" or "old")
            file_content: Optional file content (may be Base64-encoded)
            
        Returns:
            True if comment was posted successfully
        """
        # Calculate line_code
        line_code = None
        if file_content:
            logger.debug(
                f"Calculating line_code for {file_path}:{line_number} "
                f"from file_content ({len(file_content)} chars)"
            )
            line_code = self._calculate_line_code_from_content(file_path, line_number, file_content)
        
        if not line_code:
            logger.debug(f"Attempting to calculate line_code via API for {file_path}:{line_number}")
            line_code = self._calculate_line_code(project_id, file_path, line_number, mr)
        
        if not line_code or not line_code.strip():
            logger.warning(
                f"Could not calculate line_code for {file_path}:{line_number}. "
                f"file_content provided: {file_content is not None}, "
                f"file_content length: {len(file_content) if file_content else 0}. "
                "Skipping inline comment (GitLab requires line_code)."
            )
            return False
        
        # Build position dict
        position = {
            "base_sha": mr.diff_refs["base_sha"],
            "start_sha": mr.diff_refs["start_sha"],
            "head_sha": mr.diff_refs["head_sha"],
            "old_path": file_path,
            "new_path": file_path,
            "position_type": "text",
            "new_line": line_number if line_type == "new" else None,
            "old_line": line_number if line_type == "old" else None,
            "line_code": line_code,
        }
        
        logger.debug(
            f"Posting inline comment to {file_path}:{line_number} with line_code length: {len(line_code)}"
        )
        
        try:
            # Create discussion with position
            discussion_data = {"body": body, "position": position}
            self._retry_api_call(lambda: mr.discussions.create(discussion_data))
            logger.debug(f"Posted inline comment to {file_path}:{line_number}")
            return True
        except Exception as e:
            error_msg = str(e)
            if "line_code" in error_msg.lower():
                # GitLab rejects line_code for lines outside the MR diff
                # Fallback to general comment
                logger.debug(
                    f"GitLab rejected inline comment for {file_path}:{line_number} "
                    f"(line outside diff or line_code validation failed). "
                    f"Falling back to general comment. Error: {error_msg[:200]}"
                )
                try:
                    self._retry_api_call(
                        lambda: mr.notes.create({
                            "body": f"*[Comment for {file_path}:{line_number}]*\n\n{body}"
                        })
                    )
                    logger.debug(f"Posted as general comment for {file_path}:{line_number}")
                    return True
                except Exception as fallback_error:
                    logger.error(f"Failed to post as general comment: {fallback_error}")
                    raise
            raise

    def post_comment(
        self,
        project_id: str,
        merge_request_iid: int,
        body: str,
        line_number: Optional[int] = None,
        file_path: Optional[str] = None,
        line_type: str = "new",
        file_content: Optional[str] = None,
    ) -> bool:
        """Post a comment to merge request
        
        Args:
            project_id: Project ID or path
            merge_request_iid: Merge request IID
            body: Comment body (markdown supported)
            line_number: Line number for inline comment (None for general comment)
            file_path: File path for inline comment
            line_type: Line type ("new" or "old") for inline comment
            file_content: Optional file content (may be Base64-encoded)
            
        Returns:
            True if comment was posted successfully
        """
        try:
            mr = self.get_merge_request(project_id, merge_request_iid)

            if line_number and file_path:
                # Inline comment
                return self._post_inline_comment(
                    mr, project_id, body, file_path, line_number, line_type, file_content
                )
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
        from tenacity import (
            retry as tenacity_retry,
            retry_if_exception,
            stop_after_attempt,
            wait_exponential,
            wait_random,
            before_sleep_log,
        )

        def _retry_condition(exception: Exception) -> bool:
            if isinstance(exception, GitlabError):
                return _should_retry_gitlab_error(exception)
            return True  # Retry other exceptions

        # Настройка wait с jitter
        wait = wait_exponential(
            multiplier=self.retry_config.initial_delay,
            exp_base=self.retry_config.backoff_multiplier,
            min=self.retry_config.initial_delay,
            max=60.0,
        )
        if self.retry_config.jitter > 0:
            jitter_amount = self.retry_config.initial_delay * self.retry_config.jitter
            wait = wait + wait_random(-jitter_amount, jitter_amount)

        @tenacity_retry(
            stop=stop_after_attempt(self.retry_config.max_attempts),
            wait=wait,
            retry=retry_if_exception(_retry_condition),
            reraise=True,
            before_sleep=before_sleep_log(logger, logging.WARNING),
        )
        def _call_with_retry():
            return func(*args, **kwargs)

        # Вызываем функцию с retry и обрабатываем неретрайящиеся ошибки
        try:
            return _call_with_retry()
        except GitlabError as e:
            # Эти ошибки не ретраятся (логика в _retry_condition)
            # Конвертируем в RuntimeError с понятными сообщениями
            status_code = getattr(e, "response_code", None) if hasattr(e, "response_code") else None
            if status_code in (401, 403):
                logger.error(f"GitLab API authentication error: {e}")
                raise RuntimeError(f"GitLab API authentication failed: {e}") from e
            elif status_code and 400 <= status_code < 500 and status_code != 429:
                logger.error(f"GitLab API client error ({status_code}): {e}")
                raise RuntimeError(f"GitLab API client error: {e}") from e
            else:
                # Исчерпаны retry для 429/5xx
                logger.error(f"GitLab API request failed after {self.retry_config.max_attempts} attempts: {e}")
                raise RuntimeError(f"GitLab API request failed after {self.retry_config.max_attempts} attempts: {e}") from e
