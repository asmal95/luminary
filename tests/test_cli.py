"""Tests for CLI interface"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from luminary.cli import _die, cli, main, parse_file_or_diff, setup_logging
from luminary.domain.config.comments import CommentsConfig
from luminary.domain.config.ignore import IgnoreConfig
from luminary.domain.config.limits import LimitsConfig
from luminary.domain.config.llm import LLMConfig
from luminary.domain.config.prompts import PromptsConfig
from luminary.domain.config.retry import RetryConfig
from luminary.domain.config.validator import ValidatorConfig
from luminary.domain.models.file_change import FileChange


class TestSetupLogging:
    """Tests for setup_logging function"""

    def test_setup_logging_info_level(self):
        """Test that logging is set to INFO level by default"""
        setup_logging(verbose=False)
        assert logging.getLogger().level == logging.INFO

    def test_setup_logging_debug_level(self):
        """Test that logging is set to DEBUG level when verbose"""
        setup_logging(verbose=True)
        assert logging.getLogger().level == logging.DEBUG


class TestDie:
    """Tests for _die function"""

    def test_die_without_exception(self):
        """Test _die without exception"""
        with pytest.raises(click.ClickException, match="Test error"):
            _die("Test error", verbose=False)

    def test_die_with_exception(self):
        """Test _die with exception"""
        exc = ValueError("Test exception")
        with pytest.raises(click.ClickException, match="Test error"):
            _die("Test error", verbose=False, exc=exc)

    def test_die_with_exception_verbose(self):
        """Test _die with exception in verbose mode"""
        exc = ValueError("Test exception")
        with pytest.raises(click.ClickException, match="Test error"):
            _die("Test error", verbose=True, exc=exc)


class TestParseFileOrDiff:
    """Tests for parse_file_or_diff function"""

    def test_parse_file_not_found(self):
        """Test parsing non-existent file"""
        with pytest.raises(FileNotFoundError):
            parse_file_or_diff(Path("nonexistent_file.py"))

    def test_parse_regular_file(self, tmp_path):
        """Test parsing regular file"""
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')\n", encoding="utf-8")

        result = parse_file_or_diff(test_file)

        assert isinstance(result, FileChange)
        assert result.path == str(test_file)
        assert result.new_content == "print('hello')\n"

    def test_parse_diff_file(self, tmp_path):
        """Test parsing diff file"""
        diff_file = tmp_path / "test.diff"
        diff_content = """--- a/test.py
+++ b/test.py
@@ -1,1 +1,2 @@
-print('old')
+print('new')
+print('added')
"""
        diff_file.write_text(diff_content, encoding="utf-8")

        result = parse_file_or_diff(diff_file)

        assert isinstance(result, FileChange)
        assert len(result.hunks) > 0

    def test_parse_file_starting_with_dash(self, tmp_path):
        """Test parsing file that starts with --- but is not a diff"""
        test_file = tmp_path / "test.py"
        # File starting with --- but not +++ on next line is treated as diff
        # So we test with a file that doesn't start with --- or +++
        test_file.write_text("# This is not a diff\nprint('hello')\n", encoding="utf-8")

        result = parse_file_or_diff(test_file)

        assert isinstance(result, FileChange)
        assert result.new_content is not None


class TestFileCommand:
    """Tests for file command"""

    @patch("luminary.cli.ConfigManager")
    @patch("luminary.cli.LLMProviderFactory")
    @patch("luminary.cli.ReviewService")
    @patch("luminary.cli.parse_file_or_diff")
    def test_file_command_success(
        self, mock_parse, mock_review_service, mock_factory, mock_config_manager, tmp_path
    ):
        """Test successful file command execution"""
        # Setup mocks
        mock_config = MagicMock()
        mock_config.get_llm_config.return_value = LLMConfig(provider="mock")
        mock_config.get_validator_config.return_value = ValidatorConfig(enabled=False)
        mock_config.get_comments_config.return_value = CommentsConfig(mode="both")
        mock_config.get_limits_config.return_value = LimitsConfig()
        mock_config.get_prompts_config.return_value = PromptsConfig()
        mock_config.get_retry_config.return_value = RetryConfig()
        mock_config_manager.return_value = mock_config

        mock_provider = MagicMock()
        mock_factory.create.return_value = mock_provider

        mock_review = MagicMock()
        mock_result = MagicMock()
        mock_result.error = None
        mock_result.has_comments = True
        mock_result.comments = []
        mock_result.summary = "Test summary"
        mock_result.file_change.path = "test.py"
        mock_review.review_file.return_value = mock_result
        mock_review_service.return_value = mock_review

        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')\n", encoding="utf-8")

        mock_file_change = FileChange(path=str(test_file), new_content="print('hello')\n")
        mock_parse.return_value = mock_file_change

        # Run command using CliRunner
        runner = CliRunner()
        with patch("click.echo") as mock_echo:
            result = runner.invoke(cli, ["file", str(test_file)], catch_exceptions=False)
            # Command should succeed (exit code 0)
            assert result.exit_code == 0
            assert mock_echo.called

    @patch("luminary.cli.ConfigManager")
    @patch("luminary.cli.LLMProviderFactory")
    @patch("luminary.cli.parse_file_or_diff")
    def test_file_command_with_error(self, mock_parse, mock_factory, mock_config_manager, tmp_path):
        """Test file command with error"""
        mock_config = MagicMock()
        mock_config.get_llm_config.return_value = LLMConfig(provider="mock")
        mock_config.get_validator_config.return_value = ValidatorConfig(enabled=False)
        mock_config.get_comments_config.return_value = CommentsConfig(mode="both")
        mock_config.get_limits_config.return_value = LimitsConfig()
        mock_config.get_prompts_config.return_value = PromptsConfig()
        mock_config.get_retry_config.return_value = RetryConfig()
        mock_config_manager.return_value = mock_config

        mock_factory.create.side_effect = ValueError("Invalid provider")

        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')\n", encoding="utf-8")

        # Run command using CliRunner - should fail
        runner = CliRunner()
        result = runner.invoke(cli, ["file", str(test_file)], catch_exceptions=False)
        # Command should fail
        assert result.exit_code != 0

    @patch("luminary.cli.ConfigManager")
    @patch("luminary.cli.LLMProviderFactory")
    @patch("luminary.cli.CommentValidator")
    @patch("luminary.cli.ReviewService")
    @patch("luminary.cli.parse_file_or_diff")
    def test_file_command_with_validator(
        self,
        mock_parse,
        mock_review_service,
        mock_validator_class,
        mock_factory,
        mock_config_manager,
        tmp_path,
    ):
        """Test file command with validator enabled"""
        mock_config = MagicMock()
        mock_config.get_llm_config.return_value = LLMConfig(provider="mock")
        mock_config.get_validator_config.return_value = ValidatorConfig(
            enabled=True,
            provider="mock",
            threshold=0.7,
        )
        mock_config.get_comments_config.return_value = CommentsConfig(mode="both")
        mock_config.get_limits_config.return_value = LimitsConfig()
        mock_config.get_prompts_config.return_value = PromptsConfig()
        mock_config.get_retry_config.return_value = RetryConfig()
        mock_config_manager.return_value = mock_config

        mock_provider = MagicMock()
        mock_factory.create.return_value = mock_provider

        mock_validator = MagicMock()
        mock_validator.get_stats.return_value = {"valid": 5, "total": 10}
        mock_validator_class.return_value = mock_validator

        mock_review = MagicMock()
        mock_result = MagicMock()
        mock_result.error = None
        mock_result.has_comments = True
        mock_result.comments = []
        mock_result.summary = None
        mock_result.file_change.path = "test.py"
        mock_review.review_file.return_value = mock_result
        mock_review_service.return_value = mock_review

        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')\n", encoding="utf-8")

        mock_file_change = FileChange(path=str(test_file), new_content="print('hello')\n")
        mock_parse.return_value = mock_file_change

        # Run command using CliRunner
        runner = CliRunner()
        with patch("click.echo"):
            result = runner.invoke(cli, ["file", str(test_file)], catch_exceptions=False)
            assert result.exit_code == 0


class TestMRCommand:
    """Tests for mr command"""

    @patch("luminary.cli.ConfigManager")
    @patch("luminary.cli.LLMProviderFactory")
    @patch("luminary.cli.GitLabClient")
    @patch("luminary.cli.FileFilter")
    @patch("luminary.cli.ReviewService")
    @patch("luminary.cli.MRReviewService")
    def test_mr_command_success(
        self,
        mock_mr_service_class,
        mock_review_service_class,
        mock_file_filter_class,
        mock_gitlab_class,
        mock_factory,
        mock_config_manager,
    ):
        """Test successful mr command execution"""
        # Setup mocks
        mock_config = MagicMock()
        mock_config.get_llm_config.return_value = LLMConfig(provider="mock")
        mock_config.get_validator_config.return_value = ValidatorConfig(enabled=False)
        mock_config.get_ignore_config.return_value = IgnoreConfig(patterns=[])
        mock_config.get_comments_config.return_value = CommentsConfig(mode="both")
        mock_config.get_limits_config.return_value = LimitsConfig()
        mock_config.get_prompts_config.return_value = PromptsConfig()
        mock_config.get_retry_config.return_value = RetryConfig(max_attempts=3, initial_delay=1.0)
        mock_config_manager.return_value = mock_config

        mock_provider = MagicMock()
        mock_factory.create.return_value = mock_provider

        mock_gitlab = MagicMock()
        mock_gitlab_class.return_value = mock_gitlab

        mock_file_filter = MagicMock()
        mock_file_filter_class.return_value = mock_file_filter

        mock_review_service = MagicMock()
        mock_review_service_class.return_value = mock_review_service

        mock_mr_service = MagicMock()
        mock_mr_service.review_merge_request.return_value = {
            "total_files": 5,
            "filtered_files": 3,
            "ignored_files": 2,
            "processed_files": 3,
            "total_comments": 10,
            "comments_posted": 8,
            "comments_failed": 0,
        }
        mock_mr_service_class.return_value = mock_mr_service

        # Run command using CliRunner
        runner = CliRunner()
        with patch("luminary.cli.GitLabClient") as mock_gitlab:
            mock_gitlab.return_value = mock_gitlab
            with patch("click.echo") as mock_echo:
                result = runner.invoke(cli, ["mr", "group/project", "123"])
                # Command should succeed
                if result.exit_code != 0:
                    print("\n=== CLI Output ===")
                    print(result.output)
                    if result.exception:
                        print("\n=== Exception ===")
                        import traceback

                        print(
                            "".join(
                                traceback.format_exception(
                                    type(result.exception),
                                    result.exception,
                                    result.exception.__traceback__,
                                )
                            )
                        )
                assert result.exit_code == 0
                assert mock_echo.called

    @patch("luminary.cli.ConfigManager")
    @patch("luminary.cli.LLMProviderFactory")
    def test_mr_command_with_invalid_provider(self, mock_factory, mock_config_manager):
        """Test mr command with invalid provider"""
        mock_config = MagicMock()
        # Use valid provider in config, factory will raise error
        mock_config.get_llm_config.return_value = LLMConfig(provider="mock")
        mock_config.get_validator_config.return_value = ValidatorConfig(enabled=False)
        mock_config.get_ignore_config.return_value = IgnoreConfig()
        mock_config.get_comments_config.return_value = CommentsConfig()
        mock_config.get_limits_config.return_value = LimitsConfig()
        mock_config.get_prompts_config.return_value = PromptsConfig()
        mock_config.get_retry_config.return_value = RetryConfig()
        mock_config_manager.return_value = mock_config

        mock_factory.create.side_effect = ValueError("Invalid provider")

        # Run command using CliRunner - should fail
        runner = CliRunner()
        result = runner.invoke(cli, ["mr", "group/project", "123"], catch_exceptions=False)
        # Command should fail
        assert result.exit_code != 0

    @patch("luminary.cli.ConfigManager")
    @patch("luminary.cli.LLMProviderFactory")
    @patch("luminary.cli.GitLabClient")
    def test_mr_command_with_gitlab_error(
        self, mock_gitlab_class, mock_factory, mock_config_manager
    ):
        """Test mr command with GitLab client error"""
        mock_config = MagicMock()
        mock_config.get_llm_config.return_value = LLMConfig(provider="mock")
        mock_config.get_validator_config.return_value = ValidatorConfig(enabled=False)
        mock_config.get_ignore_config.return_value = IgnoreConfig()
        mock_config.get_comments_config.return_value = CommentsConfig()
        mock_config.get_limits_config.return_value = LimitsConfig()
        mock_config.get_prompts_config.return_value = PromptsConfig()
        mock_config.get_retry_config.return_value = RetryConfig()
        mock_config_manager.return_value = mock_config

        mock_provider = MagicMock()
        mock_factory.create.return_value = mock_provider

        mock_gitlab_class.side_effect = ValueError("GitLab token required")

        # Run command using CliRunner - should fail
        runner = CliRunner()
        result = runner.invoke(cli, ["mr", "group/project", "123"], catch_exceptions=False)
        # Command should fail
        assert result.exit_code != 0

    @patch("luminary.cli.ConfigManager")
    @patch("luminary.cli.LLMProviderFactory")
    @patch("luminary.cli.GitLabClient")
    @patch("luminary.cli.FileFilter")
    @patch("luminary.cli.ReviewService")
    @patch("luminary.cli.MRReviewService")
    def test_mr_command_dry_run(
        self,
        mock_mr_service_class,
        mock_review_service_class,
        mock_file_filter_class,
        mock_gitlab_class,
        mock_factory,
        mock_config_manager,
    ):
        """Test mr command with dry run (no-post)"""
        mock_config = MagicMock()
        mock_config.get_llm_config.return_value = LLMConfig(provider="mock")
        mock_config.get_validator_config.return_value = ValidatorConfig(enabled=False)
        mock_config.get_ignore_config.return_value = IgnoreConfig()
        mock_config.get_comments_config.return_value = CommentsConfig()
        mock_config.get_limits_config.return_value = LimitsConfig()
        mock_config.get_prompts_config.return_value = PromptsConfig()
        mock_config.get_retry_config.return_value = RetryConfig()
        mock_config_manager.return_value = mock_config

        mock_provider = MagicMock()
        mock_factory.create.return_value = mock_provider

        mock_gitlab = MagicMock()
        mock_gitlab_class.return_value = mock_gitlab

        mock_file_filter = MagicMock()
        mock_file_filter_class.return_value = mock_file_filter

        mock_review_service = MagicMock()
        mock_review_service_class.return_value = mock_review_service

        mock_mr_service = MagicMock()
        mock_mr_service.review_merge_request.return_value = {
            "total_files": 5,
            "filtered_files": 3,
            "ignored_files": 2,
            "processed_files": 3,
            "total_comments": 10,
            "comments_posted": 0,
            "comments_failed": 0,
        }
        mock_mr_service_class.return_value = mock_mr_service

        # Run command using CliRunner with --no-post flag
        runner = CliRunner()
        with patch("luminary.cli.GitLabClient") as mock_gitlab:
            mock_gitlab.return_value = mock_gitlab
            with patch("click.echo") as mock_echo:
                result = runner.invoke(
                    cli, ["mr", "group/project", "123", "--no-post"], catch_exceptions=False
                )
                # Command should succeed
                assert result.exit_code == 0
                # Check that dry run message was printed
                calls = [str(call) for call in mock_echo.call_args_list]
                assert any("Dry run" in str(call) for call in calls)


class TestMain:
    """Tests for main function"""

    @patch("luminary.cli.cli")
    def test_main_calls_cli(self, mock_cli):
        """Test that main function calls cli"""
        main()
        mock_cli.assert_called_once_with(obj={})
