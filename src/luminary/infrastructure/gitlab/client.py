"""GitLab API client"""

import base64
import hashlib
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
                
                # Try repository_blob API first (more reliable - returns raw file content)
                refs_to_try = [
                    mr.source_branch,  # MR source branch
                    mr.diff_refs.get("head_sha"),  # Head commit SHA
                ]
                
                for ref in refs_to_try:
                    if not ref:
                        continue
                    try:
                        # Try repository_blob API (returns raw file content as bytes)
                        # Check if method exists first (may not be available in all python-gitlab versions)
                        if hasattr(project, 'repository_blob'):
                            blob = self._retry_api_call(
                                lambda: project.repository_blob(new_path, ref=ref)
                            )
                            if blob:
                                if isinstance(blob, bytes):
                                    new_content = blob.decode('utf-8')
                                else:
                                    new_content = str(blob)
                                logger.debug(f"Successfully fetched content via repository_blob for {new_path} ({len(new_content)} chars)")
                                break
                        else:
                            logger.debug(f"repository_blob method not available, trying files.get")
                            break  # Skip to files.get fallback
                    except (AttributeError, GitlabError) as e:
                        status_code = getattr(e, 'response_code', None) if isinstance(e, GitlabError) else None
                        if status_code == 404:
                            logger.debug(f"File {new_path} not found in ref {ref}, trying next ref")
                        else:
                            logger.debug(f"repository_blob failed for {new_path}@{ref}: {e}, trying files.get")
                        continue
                    except Exception as e:
                        logger.debug(f"repository_blob exception for {new_path}@{ref}: {e}, trying files.get")
                        continue
                
                # Fallback to files.get API if repository_blob didn't work
                if not new_content:
                    try:
                        file_obj = self._retry_api_call(
                            lambda: project.files.get(new_path, ref=mr.source_branch)
                        )
                        
                        # Debug: log what we got
                        logger.debug(f"files.get returned type: {type(file_obj)}, has decode_bytes: {hasattr(file_obj, 'decode_bytes')}, has content: {hasattr(file_obj, 'content')}, has decode: {hasattr(file_obj, 'decode')}")
                        
                        # Handle different return types from python-gitlab
                        # ProjectFile.decode() doesn't take arguments - it just decodes the file content
                        if isinstance(file_obj, bytes):
                            new_content = file_obj.decode('utf-8')
                        elif hasattr(file_obj, 'decode_bytes'):
                            # decode_bytes() returns bytes, then we decode to string
                            try:
                                decoded_bytes = file_obj.decode_bytes()
                                new_content = decoded_bytes.decode('utf-8')
                            except Exception as e:
                                logger.warning(f"decode_bytes() failed for {new_path}: {e}")
                                new_content = None
                        elif hasattr(file_obj, 'content'):
                            # ProjectFile.content is Base64-encoded string in python-gitlab!
                            file_content = file_obj.content
                            if isinstance(file_content, bytes):
                                # If bytes, try to decode as Base64 first
                                try:
                                    decoded_bytes = base64.b64decode(file_content)
                                    new_content = decoded_bytes.decode('utf-8')
                                except Exception:
                                    # If not Base64, try direct decode
                                    new_content = file_content.decode('utf-8')
                            elif isinstance(file_content, str):
                                # ProjectFile.content is Base64-encoded - decode it!
                                try:
                                    decoded_bytes = base64.b64decode(file_content)
                                    new_content = decoded_bytes.decode('utf-8')
                                except Exception:
                                    # If decode fails, use as-is (shouldn't happen, but just in case)
                                    new_content = file_content
                            else:
                                new_content = str(file_content) if file_content else None
                        elif hasattr(file_obj, 'decode'):
                            # decode() without arguments - try calling it directly
                            try:
                                decoded = file_obj.decode()  # No arguments!
                                if isinstance(decoded, bytes):
                                    new_content = decoded.decode('utf-8')
                                elif isinstance(decoded, str):
                                    new_content = decoded
                                else:
                                    new_content = str(decoded)
                            except Exception as e:
                                logger.warning(f"decode() failed for {new_path}: {e}")
                                new_content = None
                        else:
                            # Last resort - convert to string
                            new_content = str(file_obj) if file_obj else None
                        
                        if new_content and len(new_content.strip()) > 0:
                            logger.debug(f"Successfully fetched content via files.get for {new_path} ({len(new_content)} chars)")
                        else:
                            logger.warning(f"files.get returned empty content for {new_path}")
                    except Exception as e:
                        logger.warning(f"Could not fetch content via files.get for {new_path}: {e}", exc_info=True)
            except Exception as e:
                logger.warning(f"Could not fetch content for {new_path}: {e}")

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
            
            # Try multiple strategies to get file content
            content = None
            refs_to_try = [
                mr.source_branch,  # MR source branch
                mr.diff_refs.get("head_sha"),  # Head commit SHA
            ]
            
            for ref in refs_to_try:
                if not ref:
                    continue
                try:
                    # Try to get file content - don't retry on 404
                    file_obj = project.files.get(file_path, ref=ref)
                    
                    # Handle different return types from python-gitlab
                    if isinstance(file_obj, bytes):
                        content = file_obj.decode('utf-8')
                    elif hasattr(file_obj, 'decode_bytes'):
                        # decode_bytes() is the correct method - returns bytes
                        try:
                            decoded_bytes = file_obj.decode_bytes()
                            content = decoded_bytes.decode('utf-8')
                        except Exception as e:
                            logger.debug(f"decode_bytes() failed: {e}")
                            raise
                    elif hasattr(file_obj, 'content'):
                        # ProjectFile.content is Base64-encoded string in python-gitlab
                        file_content = file_obj.content
                        if isinstance(file_content, bytes):
                            try:
                                decoded_bytes = base64.b64decode(file_content)
                                content = decoded_bytes.decode('utf-8')
                            except Exception:
                                content = file_content.decode('utf-8')
                        elif isinstance(file_content, str):
                            # Decode Base64 string
                            try:
                                decoded_bytes = base64.b64decode(file_content)
                                content = decoded_bytes.decode('utf-8')
                            except Exception:
                                content = file_content  # Fallback
                        else:
                            content = str(file_content)
                    elif hasattr(file_obj, 'decode'):
                        # decode() without arguments - LAST RESORT (may not work correctly)
                        try:
                            decoded_result = file_obj.decode()  # No arguments!
                            if isinstance(decoded_result, bytes):
                                content = decoded_result.decode('utf-8')
                            elif isinstance(decoded_result, str):
                                # decode() might return Base64 - try to decode
                                try:
                                    decoded_bytes = base64.b64decode(decoded_result)
                                    content = decoded_bytes.decode('utf-8')
                                except Exception:
                                    content = decoded_result
                            else:
                                content = str(decoded_result)
                        except Exception as e:
                            logger.debug(f"decode() failed: {e}")
                            raise
                    elif hasattr(file_obj, 'data'):
                        # ProjectFile object with data attribute
                        file_data = file_obj.data
                        if isinstance(file_data, bytes):
                            content = file_data.decode('utf-8')
                        else:
                            content = str(file_data)
                    else:
                        content = str(file_obj)
                    
                    if content:
                        break  # Successfully got content
                except GitlabError as e:
                    # Check if it's a 404 error (expected for new files or files not in ref)
                    status_code = e.response_code if hasattr(e, "response_code") else None
                    if status_code == 404:
                        logger.debug(f"File {file_path} not found in ref {ref} (may be new file)")
                    else:
                        logger.debug(f"Could not get file {file_path} from ref {ref}: {e}")
                    continue
                except Exception as e:
                    # Other exceptions (not GitlabError) - log at debug level
                    logger.debug(f"Could not get file {file_path} from ref {ref}: {e}")
                    continue
            
            if not content:
                logger.debug(f"Empty content for {file_path} after trying all refs")
                return None
            
            # Get the specific line (line_number is 1-based)
            # Use splitlines() to handle different line endings (\n, \r\n, \r)
            lines = content.splitlines()
            if 1 <= line_number <= len(lines):
                line_content = lines[line_number - 1]
                # GitLab line_code format: SHA256 hash of the line content
                # Some GitLab versions require this to match the exact line
                line_code = hashlib.sha256(line_content.encode('utf-8')).hexdigest()
                return line_code
            else:
                logger.debug(f"Line {line_number} out of range for {file_path} (file has {len(lines)} lines, content: {len(content)} chars)")
        except Exception as e:
            logger.debug(f"Could not calculate line_code for {file_path}:{line_number}: {e}")
        return None

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
            
        Returns:
            True if comment was posted successfully
        """
        try:
            mr = self.get_merge_request(project_id, merge_request_iid)

            if line_number and file_path:
                # Inline comment - need to calculate line_code for GitLab API
                # line_code is required by GitLab API for inline comments
                # Try using provided file_content first, then fall back to fetching from API
                line_code = None
                if file_content:
                    try:
                        # Check if file_content is Base64-encoded (common with GitLab API)
                        # Base64 strings typically don't have newlines and are longer than decoded content
                        decoded_content = file_content
                        if isinstance(file_content, str) and len(file_content) > 0:
                            # Check if it looks like Base64 (only base64 chars, no newlines for short strings)
                            is_likely_base64 = (
                                all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n' for c in file_content[:100])
                                and '\n' not in file_content[:200]  # Base64 usually has no newlines in content
                                and len(file_content) > 50  # Small files might not be Base64
                            )
                            
                            if is_likely_base64:
                                try:
                                    decoded_bytes = base64.b64decode(file_content)
                                    decoded_content = decoded_bytes.decode('utf-8')
                                    logger.debug(f"Decoded Base64 content for {file_path} ({len(decoded_content)} chars after decode)")
                                except Exception as e:
                                    logger.debug(f"Content doesn't seem to be Base64: {e}, using as-is")
                                    decoded_content = file_content
                        
                        # Use splitlines() to handle different line endings
                        lines = decoded_content.splitlines()
                        logger.debug(
                            f"Calculating line_code for {file_path}:{line_number} "
                            f"from file_content ({len(lines)} lines total, {len(decoded_content)} chars after decode)"
                        )
                        if 1 <= line_number <= len(lines):
                            line_content = lines[line_number - 1]
                            line_code = hashlib.sha256(line_content.encode('utf-8')).hexdigest()
                            logger.debug(f"Successfully calculated line_code from file_content: {line_code[:16]}...")
                        else:
                            # Debug: show first 200 chars to understand the structure
                            preview = decoded_content[:200].replace('\n', '\\n').replace('\r', '\\r')
                            logger.warning(
                                f"Line {line_number} out of range for {file_path} "
                                f"(file has {len(lines)} lines, content length: {len(decoded_content)} chars after decode). "
                                f"Content preview: {preview[:100]}..."
                            )
                    except Exception as e:
                        logger.warning(f"Could not calculate line_code from provided content: {e}", exc_info=True)
                else:
                    logger.debug(f"No file_content provided for {file_path}, will try API")
                
                # Fall back to API if file_content not available or failed
                if not line_code:
                    logger.debug(f"Attempting to calculate line_code via API for {file_path}:{line_number}")
                    line_code = self._calculate_line_code(
                        project_id, file_path, line_number, mr
                    )
                
                if not line_code:
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
                    "line_code": line_code,  # Required by GitLab API
                }
                
                self._retry_api_call(
                    lambda: mr.discussions.create({"body": body, "position": position})
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
                # 404 is expected for new files, so log at debug level instead of error
                if status_code and 400 <= status_code < 500 and status_code != 429:
                    if status_code == 404:
                        logger.debug(f"GitLab API 404 (file not found): {e}")
                        raise RuntimeError(f"GitLab API client error: {e}") from e
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
