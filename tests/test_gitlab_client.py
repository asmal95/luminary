"""Tests for GitLab client"""

from __future__ import annotations

import base64
import hashlib
import os
from unittest.mock import Mock, MagicMock, patch, call
from typing import Dict

import pytest
import gitlab
from gitlab.exceptions import GitlabError

from luminary.domain.models.file_change import FileChange, Hunk
from luminary.infrastructure.gitlab.client import GitLabClient


class TestGitLabClientInit:
    """Tests for GitLabClient initialization"""

    def test_init_with_explicit_params(self):
        """Test initialization with explicit parameters"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            client = GitLabClient(
                gitlab_url="https://custom.gitlab.com",
                private_token="test-token-123",
                max_retries=5,
                retry_delay=2.0,
            )
            
            assert client.gitlab_url == "https://custom.gitlab.com"
            assert client.private_token == "test-token-123"
            assert client.max_retries == 5
            assert client.retry_delay == 2.0
            mock_gitlab_class.assert_called_once_with("https://custom.gitlab.com", private_token="test-token-123")
            mock_gl.auth.assert_called_once()

    def test_init_from_env_vars(self, monkeypatch):
        """Test initialization from environment variables"""
        monkeypatch.setenv("GITLAB_URL", "https://env.gitlab.com")
        monkeypatch.setenv("GITLAB_TOKEN", "env-token-456")
        
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            client = GitLabClient()
            
            assert client.gitlab_url == "https://env.gitlab.com"
            assert client.private_token == "env-token-456"
            mock_gitlab_class.assert_called_once_with("https://env.gitlab.com", private_token="env-token-456")

    def test_init_default_url(self, monkeypatch):
        """Test initialization with default URL when env var not set"""
        monkeypatch.delenv("GITLAB_URL", raising=False)
        monkeypatch.setenv("GITLAB_TOKEN", "token-789")
        
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            client = GitLabClient()
            
            assert client.gitlab_url == "https://gitlab.com"
            assert client.private_token == "token-789"

    def test_init_no_token_raises_error(self, monkeypatch):
        """Test that missing token raises ValueError"""
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        
        with pytest.raises(ValueError, match="GitLab private token is required"):
            GitLabClient()


class TestRetryLogic:
    """Tests for retry logic"""

    def test_retry_on_server_error_succeeds(self):
        """Test retry succeeds after server error"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            client = GitLabClient(private_token="test-token", max_retries=3)
            
            call_count = {"n": 0}
            
            def failing_func():
                call_count["n"] += 1
                if call_count["n"] < 3:
                    error = GitlabError("Server error")
                    error.response_code = 500
                    raise error
                return "success"
            
            with patch("tenacity.nap.sleep"):  # Patch tenacity's sleep instead
                result = client._retry_api_call(failing_func)
                assert result == "success"
                assert call_count["n"] == 3

    def test_retry_on_rate_limit_succeeds(self):
        """Test retry succeeds after rate limit"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            client = GitLabClient(private_token="test-token", max_retries=2)
            
            call_count = {"n": 0}
            
            def failing_func():
                call_count["n"] += 1
                if call_count["n"] < 2:
                    error = GitlabError("Rate limited")
                    error.response_code = 429
                    raise error
                return "success"
            
            with patch("tenacity.nap.sleep"):  # Patch tenacity's sleep instead
                result = client._retry_api_call(failing_func)
                assert result == "success"

    def test_no_retry_on_auth_error(self):
        """Test that auth errors (401, 403) are not retried"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            client = GitLabClient(private_token="test-token")
            
            def failing_func():
                error = GitlabError("Unauthorized")
                error.response_code = 401
                raise error
            
            with pytest.raises(RuntimeError, match="GitLab API authentication failed"):
                client._retry_api_call(failing_func)

    def test_no_retry_on_404(self):
        """Test that 404 errors are not retried"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            client = GitLabClient(private_token="test-token")
            
            def failing_func():
                error = GitlabError("Not found")
                error.response_code = 404
                raise error
            
            with pytest.raises(RuntimeError, match="GitLab API client error"):
                client._retry_api_call(failing_func)

    def test_retry_fails_after_max_attempts(self):
        """Test that retry fails after max attempts"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            client = GitLabClient(private_token="test-token", max_retries=2)
            
            def failing_func():
                error = GitlabError("Server error")
                error.response_code = 500
                raise error
            
            with patch("tenacity.nap.sleep"):  # Patch tenacity's sleep instead
                with pytest.raises(RuntimeError, match="GitLab API request failed after 2 attempts"):
                    client._retry_api_call(failing_func)

    def test_exponential_backoff(self):
        """Test that retry delay increases exponentially"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            client = GitLabClient(private_token="test-token", max_retries=3, retry_delay=1.0)
            
            call_count = {"n": 0}
            sleep_calls = []
            
            def failing_func():
                call_count["n"] += 1
                if call_count["n"] < 3:
                    error = GitlabError("Server error")
                    error.response_code = 500
                    raise error
                return "success"
            
            def mock_sleep(delay):
                sleep_calls.append(delay)
            
            with patch("tenacity.nap.sleep", side_effect=mock_sleep):  # Patch tenacity's sleep instead
                client._retry_api_call(failing_func)
                # First retry: 1.0 * 2^0 = 1.0, second retry: 1.0 * 2^1 = 2.0
                # Note: with jitter default (0.1), delays will vary slightly, so we check they're approximately correct
                assert len(sleep_calls) == 2
                # Allow some variance due to jitter
                assert 0.8 <= sleep_calls[0] <= 1.2
                assert 1.6 <= sleep_calls[1] <= 2.4

    def test_retry_with_jitter(self):
        """Test that jitter adds randomness to retry delays"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            from luminary.infrastructure.http_client import RetryConfig
            retry_config = RetryConfig(max_attempts=3, initial_delay=1.0, backoff_multiplier=2, jitter=0.2)
            client = GitLabClient(private_token="test-token", retry_config=retry_config)
            
            call_count = {"n": 0}
            sleep_calls = []
            
            def failing_func():
                call_count["n"] += 1
                if call_count["n"] < 3:
                    error = GitlabError("Server error")
                    error.response_code = 500
                    raise error
                return "success"
            
            def mock_sleep(delay):
                sleep_calls.append(delay)
            
            with patch("tenacity.nap.sleep", side_effect=mock_sleep):
                client._retry_api_call(failing_func)
                assert len(sleep_calls) == 2
                # With jitter=0.2, delays should vary by +/-20%
                # First delay: 1.0 * 2^0 = 1.0 +/- 0.2
                assert 0.8 <= sleep_calls[0] <= 1.2
                # Second delay: 1.0 * 2^1 = 2.0 +/- 0.4
                assert 1.6 <= sleep_calls[1] <= 2.4

    def test_retry_with_custom_backoff_multiplier(self):
        """Test that custom backoff_multiplier works correctly"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            from luminary.infrastructure.http_client import RetryConfig
            retry_config = RetryConfig(max_attempts=3, initial_delay=1.0, backoff_multiplier=3, jitter=0)
            client = GitLabClient(private_token="test-token", retry_config=retry_config)
            
            call_count = {"n": 0}
            sleep_calls = []
            
            def failing_func():
                call_count["n"] += 1
                if call_count["n"] < 3:
                    error = GitlabError("Server error")
                    error.response_code = 500
                    raise error
                return "success"
            
            def mock_sleep(delay):
                sleep_calls.append(delay)
            
            with patch("tenacity.nap.sleep", side_effect=mock_sleep):
                client._retry_api_call(failing_func)
                assert len(sleep_calls) == 2
                # With backoff_multiplier=3: first delay = 1.0, second delay = 3.0
                assert sleep_calls[0] == pytest.approx(1.0, rel=0.1)
                assert sleep_calls[1] == pytest.approx(3.0, rel=0.1)


class TestGetMergeRequest:
    """Tests for get_merge_request"""

    def test_get_merge_request_success(self):
        """Test successfully getting merge request"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            mock_project = MagicMock()
            mock_mr = MagicMock()
            mock_project.mergerequests.get.return_value = mock_mr
            mock_gl.projects.get.return_value = mock_project
            
            client = GitLabClient(private_token="test-token")
            result = client.get_merge_request("group/project", 123)
            
            assert result == mock_mr
            mock_gl.projects.get.assert_called_once_with("group/project")
            mock_project.mergerequests.get.assert_called_once_with(123)


class TestParseDiffToHunks:
    """Tests for _parse_diff_to_hunks"""

    def test_parse_empty_diff(self):
        """Test parsing empty diff"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            client = GitLabClient(private_token="test-token")
            hunks = client._parse_diff_to_hunks("")
            
            assert hunks == []

    def test_parse_single_hunk(self):
        """Test parsing diff with single hunk"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            client = GitLabClient(private_token="test-token")
            diff = "@@ -1,3 +1,4 @@\n line1\n+line2\n line3\n"
            hunks = client._parse_diff_to_hunks(diff)
            
            assert len(hunks) == 1
            assert hunks[0].old_start == 1
            assert hunks[0].old_count == 3
            assert hunks[0].new_start == 1
            assert hunks[0].new_count == 4
            assert hunks[0].lines == [" line1", "+line2", " line3"]

    def test_parse_multiple_hunks(self):
        """Test parsing diff with multiple hunks"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            client = GitLabClient(private_token="test-token")
            diff = "@@ -1,2 +1,2 @@\n line1\n line2\n@@ -5,2 +6,3 @@\n line5\n+line6\n"
            hunks = client._parse_diff_to_hunks(diff)
            
            assert len(hunks) == 2
            assert hunks[0].old_start == 1
            assert hunks[0].old_count == 2
            assert hunks[1].old_start == 5
            assert hunks[1].old_count == 2

    def test_parse_hunk_with_default_count(self):
        """Test parsing hunk header without explicit count"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            client = GitLabClient(private_token="test-token")
            diff = "@@ -10 +15 @@\n line10\n"
            hunks = client._parse_diff_to_hunks(diff)
            
            assert len(hunks) == 1
            assert hunks[0].old_start == 10
            assert hunks[0].old_count == 1  # Default count
            assert hunks[0].new_start == 15
            assert hunks[0].new_count == 1


class TestParseGitLabChange:
    """Tests for _parse_gitlab_change"""

    def test_parse_added_file(self):
        """Test parsing added file change"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            mock_project = MagicMock()
            mock_gl.projects.get.return_value = mock_project
            
            # Mock repository_blob
            mock_project.repository_blob = MagicMock(return_value=b"file content")
            
            mock_mr = MagicMock()
            mock_mr.source_branch = "feature-branch"
            mock_mr.diff_refs = {"head_sha": "abc123"}
            
            client = GitLabClient(private_token="test-token")
            
            change = {
                "old_path": None,
                "new_path": "new_file.py",
                "diff": "@@ -0,0 +1,2 @@\n+line1\n+line2\n",
            }
            
            file_change = client._parse_gitlab_change(change, "group/project", mock_mr)
            
            assert file_change is not None
            assert file_change.path == "new_file.py"
            assert file_change.status == "added"
            assert file_change.new_content == "file content"
            assert len(file_change.hunks) == 1

    def test_parse_deleted_file(self):
        """Test parsing deleted file change"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            mock_mr = MagicMock()
            
            client = GitLabClient(private_token="test-token")
            
            change = {
                "old_path": "old_file.py",
                "new_path": None,
                "diff": "@@ -1,2 +0,0 @@\n-line1\n-line2\n",
            }
            
            file_change = client._parse_gitlab_change(change, "group/project", mock_mr)
            
            assert file_change is not None
            assert file_change.path == "old_file.py"
            assert file_change.status == "deleted"
            assert file_change.new_content is None

    def test_parse_renamed_file(self):
        """Test parsing renamed file change"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            mock_project = MagicMock()
            mock_gl.projects.get.return_value = mock_project
            mock_project.repository_blob = MagicMock(return_value=b"content")
            
            mock_mr = MagicMock()
            mock_mr.source_branch = "feature-branch"
            mock_mr.diff_refs = {"head_sha": "abc123"}
            
            client = GitLabClient(private_token="test-token")
            
            change = {
                "old_path": "old_name.py",
                "new_path": "new_name.py",
                "diff": "",
            }
            
            file_change = client._parse_gitlab_change(change, "group/project", mock_mr)
            
            assert file_change is not None
            assert file_change.path == "new_name.py"
            assert file_change.old_path == "old_name.py"
            assert file_change.status == "renamed"

    def test_parse_modified_file(self):
        """Test parsing modified file change"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            mock_project = MagicMock()
            mock_gl.projects.get.return_value = mock_project
            mock_project.repository_blob = MagicMock(return_value=b"new content")
            
            mock_mr = MagicMock()
            mock_mr.source_branch = "feature-branch"
            mock_mr.diff_refs = {"head_sha": "abc123"}
            
            client = GitLabClient(private_token="test-token")
            
            change = {
                "old_path": "file.py",
                "new_path": "file.py",
                "diff": "@@ -1,1 +1,2 @@\n line1\n+line2\n",
            }
            
            file_change = client._parse_gitlab_change(change, "group/project", mock_mr)
            
            assert file_change is not None
            assert file_change.path == "file.py"
            assert file_change.status == "modified"
            assert file_change.old_path is None  # Same path, so no old_path

    def test_parse_file_with_base64_content(self):
        """Test parsing file with Base64-encoded content"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            mock_project = MagicMock()
            mock_gl.projects.get.return_value = mock_project
            
            # Mock repository_blob to not exist (hasattr returns False)
            del mock_project.repository_blob
            
            # Mock files.get with Base64 content
            mock_file = MagicMock()
            mock_file.content = base64.b64encode(b"decoded content").decode('utf-8')
            # Don't have decode_bytes method
            del mock_file.decode_bytes
            mock_project.files.get.return_value = mock_file
            
            mock_mr = MagicMock()
            mock_mr.source_branch = "feature-branch"
            mock_mr.diff_refs = {"head_sha": "abc123"}
            
            client = GitLabClient(private_token="test-token")
            
            change = {
                "old_path": None,
                "new_path": "file.py",
                "diff": "",
            }
            
            file_change = client._parse_gitlab_change(change, "group/project", mock_mr)
            
            assert file_change is not None
            assert file_change.new_content == "decoded content"

    def test_parse_file_with_decode_bytes_method(self):
        """Test parsing file using decode_bytes method"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            mock_project = MagicMock()
            mock_gl.projects.get.return_value = mock_project
            
            mock_file = MagicMock()
            mock_file.decode_bytes.return_value = b"decoded bytes"
            mock_project.repository_blob = None
            mock_project.files.get.return_value = mock_file
            
            mock_mr = MagicMock()
            mock_mr.source_branch = "feature-branch"
            mock_mr.diff_refs = {"head_sha": "abc123"}
            
            client = GitLabClient(private_token="test-token")
            
            change = {
                "old_path": None,
                "new_path": "file.py",
                "diff": "",
            }
            
            file_change = client._parse_gitlab_change(change, "group/project", mock_mr)
            
            assert file_change is not None
            assert file_change.new_content == "decoded bytes"

    def test_parse_file_no_path_returns_none(self):
        """Test parsing change with no path returns None"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            mock_mr = MagicMock()
            client = GitLabClient(private_token="test-token")
            
            change = {
                "old_path": None,
                "new_path": None,
                "diff": "",
            }
            
            file_change = client._parse_gitlab_change(change, "group/project", mock_mr)
            
            assert file_change is None

    def test_parse_file_handles_fetch_error_gracefully(self):
        """Test that file fetch errors are handled gracefully"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            mock_project = MagicMock()
            mock_gl.projects.get.return_value = mock_project
            
            # Mock repository_blob to raise 404 error
            error = GitlabError("404 Not Found")
            error.response_code = 404
            mock_project.repository_blob = MagicMock(side_effect=error)
            
            # Mock files.get to also fail
            error2 = GitlabError("404 Not Found")
            error2.response_code = 404
            mock_project.files.get.side_effect = error2
            
            mock_mr = MagicMock()
            mock_mr.source_branch = "feature-branch"
            mock_mr.diff_refs = {"head_sha": "abc123"}
            
            client = GitLabClient(private_token="test-token")
            
            change = {
                "old_path": None,
                "new_path": "file.py",
                "diff": "@@ -0,0 +1,1 @@\n+line1\n",
            }
            
            file_change = client._parse_gitlab_change(change, "group/project", mock_mr)
            
            # Should still create FileChange even if content fetch fails
            assert file_change is not None
            assert file_change.path == "file.py"
            assert file_change.new_content is None  # Content fetch failed


class TestCalculateLineCode:
    """Tests for _calculate_line_code"""

    def test_calculate_line_code_success(self):
        """Test successfully calculating line_code"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            mock_project = MagicMock()
            mock_gl.projects.get.return_value = mock_project
            
            mock_file = MagicMock()
            mock_file.decode_bytes.return_value = b"line1\nline2\nline3\n"
            mock_project.files.get.return_value = mock_file
            
            mock_mr = MagicMock()
            mock_mr.source_branch = "feature-branch"
            mock_mr.diff_refs = {"head_sha": "abc123"}
            
            client = GitLabClient(private_token="test-token")
            
            file_path = "test.py"
            line_number = 2
            line_code = client._calculate_line_code("group/project", file_path, line_number, mock_mr)
            
            assert line_code is not None
            # Format: SHA1_old_line_new_line
            expected_sha = hashlib.sha1(file_path.encode('utf-8')).hexdigest()
            assert line_code == f"{expected_sha}_2_2"

    def test_calculate_line_code_out_of_range(self):
        """Test line_code calculation when line is out of range"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            mock_project = MagicMock()
            mock_gl.projects.get.return_value = mock_project
            
            mock_file = MagicMock()
            mock_file.decode_bytes.return_value = b"line1\n"
            mock_project.files.get.return_value = mock_file
            
            mock_mr = MagicMock()
            mock_mr.source_branch = "feature-branch"
            mock_mr.diff_refs = {"head_sha": "abc123"}
            
            client = GitLabClient(private_token="test-token")
            
            line_code = client._calculate_line_code("group/project", "test.py", 10, mock_mr)
            
            assert line_code is None

    def test_calculate_line_code_file_not_found(self):
        """Test line_code calculation when file is not found"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            mock_project = MagicMock()
            mock_gl.projects.get.return_value = mock_project
            
            error = GitlabError("404 Not Found")
            error.response_code = 404
            mock_project.files.get.side_effect = error
            
            mock_mr = MagicMock()
            mock_mr.source_branch = "feature-branch"
            mock_mr.diff_refs = {"head_sha": "abc123"}
            
            client = GitLabClient(private_token="test-token")
            
            line_code = client._calculate_line_code("group/project", "test.py", 1, mock_mr)
            
            assert line_code is None

    def test_calculate_line_code_with_base64_content(self):
        """Test line_code calculation with Base64-encoded content"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            mock_project = MagicMock()
            mock_gl.projects.get.return_value = mock_project
            
            # Mock file object with Base64 content
            mock_file = MagicMock(spec=[])  # Don't include any default methods
            mock_file.content = base64.b64encode(b"line1\nline2\nline3\n").decode('utf-8')
            # Ensure decode_bytes doesn't exist
            if hasattr(mock_file, 'decode_bytes'):
                delattr(mock_file, 'decode_bytes')
            if hasattr(mock_file, 'decode'):
                delattr(mock_file, 'decode')
            
            # files.get is called directly in _calculate_line_code (not through retry)
            mock_project.files.get = lambda path, ref: mock_file
            
            mock_mr = MagicMock()
            mock_mr.source_branch = "feature-branch"
            mock_mr.diff_refs = {"head_sha": "abc123"}
            
            client = GitLabClient(private_token="test-token")
            
            file_path = "test.py"
            line_code = client._calculate_line_code("group/project", file_path, 2, mock_mr)
            
            assert line_code is not None
            expected_sha = hashlib.sha1(file_path.encode('utf-8')).hexdigest()
            assert line_code == f"{expected_sha}_2_2"


class TestGetMergeRequestChanges:
    """Tests for get_merge_request_changes"""

    def test_get_merge_request_changes_success(self):
        """Test successfully getting MR changes"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            mock_project = MagicMock()
            mock_gl.projects.get.return_value = mock_project
            
            mock_mr = MagicMock()
            mock_mr.changes.return_value = {
                "changes": [
                    {
                        "old_path": None,
                        "new_path": "file1.py",
                        "diff": "@@ -0,0 +1,1 @@\n+line1\n",
                    },
                    {
                        "old_path": "file2.py",
                        "new_path": "file2.py",
                        "diff": "@@ -1,1 +1,2 @@\n line1\n+line2\n",
                    },
                ]
            }
            mock_mr.source_branch = "feature-branch"
            mock_mr.diff_refs = {"head_sha": "abc123"}
            
            mock_project.repository_blob = MagicMock(return_value=b"content")
            mock_project.mergerequests.get.return_value = mock_mr
            
            client = GitLabClient(private_token="test-token")
            file_changes = client.get_merge_request_changes("group/project", 123)
            
            assert len(file_changes) == 2
            assert file_changes[0].path == "file1.py"
            assert file_changes[1].path == "file2.py"

    def test_get_merge_request_changes_handles_parse_error(self):
        """Test that parse errors are handled gracefully"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            mock_project = MagicMock()
            mock_gl.projects.get.return_value = mock_project
            
            mock_mr = MagicMock()
            mock_mr.changes.return_value = {
                "changes": [
                    {
                        "old_path": None,
                        "new_path": "file1.py",
                        "diff": "",
                    },
                    {
                        "old_path": None,
                        "new_path": None,  # Invalid change
                        "diff": "",
                    },
                ]
            }
            mock_mr.source_branch = "feature-branch"
            mock_mr.diff_refs = {"head_sha": "abc123"}
            
            mock_project.repository_blob = MagicMock(return_value=b"content")
            mock_project.mergerequests.get.return_value = mock_mr
            
            client = GitLabClient(private_token="test-token")
            file_changes = client.get_merge_request_changes("group/project", 123)
            
            # Should return only valid changes
            assert len(file_changes) == 1
            assert file_changes[0].path == "file1.py"


class TestPostComment:
    """Tests for post_comment"""

    def test_post_general_comment(self):
        """Test posting general comment (not inline)"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            mock_project = MagicMock()
            mock_gl.projects.get.return_value = mock_project
            
            mock_mr = MagicMock()
            mock_notes = MagicMock()
            mock_mr.notes = mock_notes
            mock_project.mergerequests.get.return_value = mock_mr
            
            client = GitLabClient(private_token="test-token")
            result = client.post_comment("group/project", 123, "Test comment")
            
            assert result is True
            mock_notes.create.assert_called_once_with({"body": "Test comment"})

    def test_post_inline_comment_with_file_content(self):
        """Test posting inline comment using provided file_content"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            mock_project = MagicMock()
            mock_gl.projects.get.return_value = mock_project
            
            mock_mr = MagicMock()
            mock_mr.diff_refs = {
                "base_sha": "base123",
                "start_sha": "start123",
                "head_sha": "head123",
            }
            mock_discussions = MagicMock()
            mock_mr.discussions = mock_discussions
            mock_project.mergerequests.get.return_value = mock_mr
            
            client = GitLabClient(private_token="test-token")
            
            file_content = "line1\nline2\nline3\n"
            result = client.post_comment(
                "group/project",
                123,
                "Inline comment",
                line_number=2,
                file_path="test.py",
                file_content=file_content,
            )
            
            assert result is True
            mock_discussions.create.assert_called_once()
            call_args = mock_discussions.create.call_args[0][0]
            assert call_args["body"] == "Inline comment"
            assert "position" in call_args
            assert call_args["position"]["new_line"] == 2
            assert call_args["position"]["line_code"] is not None

    def test_post_inline_comment_with_base64_file_content(self):
        """Test posting inline comment with Base64-encoded file_content"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            mock_project = MagicMock()
            mock_gl.projects.get.return_value = mock_project
            
            mock_mr = MagicMock()
            mock_mr.diff_refs = {
                "base_sha": "base123",
                "start_sha": "start123",
                "head_sha": "head123",
            }
            mock_discussions = MagicMock()
            mock_mr.discussions = mock_discussions
            mock_project.mergerequests.get.return_value = mock_mr
            
            client = GitLabClient(private_token="test-token")
            
            # Base64-encoded content - make it longer so it's detected as Base64
            # The detection logic checks if content > 50 chars and has base64 chars
            long_content = "line1\nline2\nline3\n" * 10  # Make it long enough
            file_content_b64 = base64.b64encode(long_content.encode('utf-8')).decode('utf-8')
            result = client.post_comment(
                "group/project",
                123,
                "Inline comment",
                line_number=2,
                file_path="test.py",
                file_content=file_content_b64,
            )
            
            assert result is True
            mock_discussions.create.assert_called_once()

    def test_post_inline_comment_falls_back_to_api(self):
        """Test posting inline comment falls back to API when file_content not provided"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            mock_project = MagicMock()
            mock_gl.projects.get.return_value = mock_project
            
            mock_file = MagicMock()
            mock_file.decode_bytes.return_value = b"line1\nline2\nline3\n"
            mock_project.files.get.return_value = mock_file
            
            mock_mr = MagicMock()
            mock_mr.source_branch = "feature-branch"
            mock_mr.diff_refs = {
                "base_sha": "base123",
                "start_sha": "start123",
                "head_sha": "head123",
            }
            mock_discussions = MagicMock()
            mock_mr.discussions = mock_discussions
            mock_project.mergerequests.get.return_value = mock_mr
            
            client = GitLabClient(private_token="test-token")
            
            result = client.post_comment(
                "group/project",
                123,
                "Inline comment",
                line_number=2,
                file_path="test.py",
            )
            
            assert result is True
            mock_discussions.create.assert_called_once()

    def test_post_inline_comment_no_line_code_returns_false(self):
        """Test that posting inline comment without line_code returns False"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            mock_project = MagicMock()
            mock_gl.projects.get.return_value = mock_project
            
            # Make file fetch fail
            error = GitlabError("404 Not Found")
            error.response_code = 404
            mock_project.files.get.side_effect = error
            
            mock_mr = MagicMock()
            mock_mr.source_branch = "feature-branch"
            mock_mr.diff_refs = {"head_sha": "abc123"}
            mock_project.mergerequests.get.return_value = mock_mr
            
            client = GitLabClient(private_token="test-token")
            
            result = client.post_comment(
                "group/project",
                123,
                "Inline comment",
                line_number=2,
                file_path="test.py",
            )
            
            assert result is False

    def test_post_inline_comment_falls_back_to_general_on_line_code_error(self):
        """Test that inline comment falls back to general comment on line_code error"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            mock_project = MagicMock()
            mock_gl.projects.get.return_value = mock_project
            
            mock_mr = MagicMock()
            mock_mr.diff_refs = {
                "base_sha": "base123",
                "start_sha": "start123",
                "head_sha": "head123",
            }
            
            mock_discussions = MagicMock()
            error = Exception("line_code validation failed")
            mock_discussions.create.side_effect = error
            mock_mr.discussions = mock_discussions
            
            mock_notes = MagicMock()
            mock_mr.notes = mock_notes
            
            mock_project.mergerequests.get.return_value = mock_mr
            
            client = GitLabClient(private_token="test-token")
            
            file_content = "line1\nline2\nline3\n"
            result = client.post_comment(
                "group/project",
                123,
                "Inline comment",
                line_number=2,
                file_path="test.py",
                file_content=file_content,
            )
            
            assert result is True
            # Should fall back to general comment
            mock_notes.create.assert_called_once()
            call_args = mock_notes.create.call_args[0][0]
            assert "[Comment for test.py:2]" in call_args["body"]

    def test_post_inline_comment_with_old_line_type(self):
        """Test posting inline comment for old line"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            mock_project = MagicMock()
            mock_gl.projects.get.return_value = mock_project
            
            mock_mr = MagicMock()
            mock_mr.diff_refs = {
                "base_sha": "base123",
                "start_sha": "start123",
                "head_sha": "head123",
            }
            mock_discussions = MagicMock()
            mock_mr.discussions = mock_discussions
            mock_project.mergerequests.get.return_value = mock_mr
            
            client = GitLabClient(private_token="test-token")
            
            file_content = "line1\nline2\nline3\n"
            result = client.post_comment(
                "group/project",
                123,
                "Inline comment",
                line_number=2,
                file_path="test.py",
                line_type="old",
                file_content=file_content,
            )
            
            assert result is True
            call_args = mock_discussions.create.call_args[0][0]
            assert call_args["position"]["old_line"] == 2
            assert call_args["position"]["new_line"] is None

    def test_post_comment_handles_exception(self):
        """Test that exceptions in post_comment are handled gracefully"""
        with patch("luminary.infrastructure.gitlab.client.gitlab.Gitlab") as mock_gitlab_class:
            mock_gl = MagicMock()
            mock_gitlab_class.return_value = mock_gl
            
            mock_project = MagicMock()
            mock_gl.projects.get.return_value = mock_project
            
            mock_project.mergerequests.get.side_effect = Exception("API Error")
            
            client = GitLabClient(private_token="test-token")
            
            result = client.post_comment("group/project", 123, "Test comment")
            
            assert result is False
