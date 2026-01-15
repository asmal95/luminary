"""CLI interface for Luminary"""

import logging
import sys
from pathlib import Path

import click

from luminary.application.mr_review_service import MRReviewService
from luminary.application.review_service import ReviewService
from luminary.domain.models.file_change import FileChange
from luminary.domain.validators.comment_validator import CommentValidator
from luminary.infrastructure.config.config_manager import ConfigManager
from luminary.infrastructure.diff_parser import parse_file_content, parse_unified_diff
from luminary.infrastructure.file_filter import FileFilter
from luminary.infrastructure.gitlab.client import GitLabClient
from luminary.infrastructure.llm.factory import LLMProviderFactory


def setup_logging(verbose: bool = False):
    """Setup logging configuration"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _die(logger: logging.Logger, message: str, verbose: bool, exc: Exception | None = None) -> None:
    """Exit with a user-friendly error message; include stack trace only in verbose logs."""
    if exc is not None:
        logger.error(message, exc_info=verbose)
    else:
        logger.error(message)
    raise click.ClickException(message)


def parse_file_or_diff(file_path: Path) -> FileChange:
    """Parse a file or diff into FileChange object
    
    Detects if file is a diff (starts with ---) or regular file.
    
    Args:
        file_path: Path to file or diff
        
    Returns:
        FileChange object
    """
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    # Read file content
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Check if it's a diff (starts with --- or +++)
    if content.startswith("--- ") or content.startswith("+++ "):
        # Parse as unified diff
        return parse_unified_diff(content, str(file_path))
    else:
        # Parse as regular file
        return parse_file_content(file_path)


@click.group()
@click.option(
    "--verbose", "-v", is_flag=True, help="Enable verbose logging"
)
@click.option(
    "--config",
    type=click.Path(exists=True, path_type=Path),
    help="Path to .ai-reviewer.yml config file",
)
@click.pass_context
def cli(ctx, verbose: bool, config: Path):
    """Luminary - AI Code Reviewer for GitLab"""
    ctx.ensure_object(dict)
    setup_logging(verbose)
    ctx.obj["config_path"] = config
    ctx.obj["verbose"] = verbose


@cli.command()
@click.argument("file_path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--provider",
    type=str,
    help="LLM provider to use (mock, openrouter, openai, deepseek, vllm). Overrides config.",
)
@click.option(
    "--comments-mode",
    type=click.Choice(["inline", "summary", "both"], case_sensitive=False),
    help="Comment mode: inline, summary, both. Overrides config.",
)
@click.option(
    "--no-validate",
    is_flag=True,
    help="Disable comment validation",
)
@click.pass_context
def file(ctx, file_path: Path, provider: str, comments_mode: str, no_validate: bool):
    """Review a single file using AI.
    
    FILE_PATH: Path to the file or diff to review
    """
    logger = logging.getLogger(__name__)
    logger.info(f"Starting review for: {file_path}")

    try:
        config_manager = ConfigManager(config_path=ctx.obj.get("config_path"))
        llm_config = config_manager.get_llm_config()
        validator_config = config_manager.get_validator_config()
        comments_config = config_manager.get_comments_config()
        limits_config = config_manager.get_limits_config()
        prompts_config = config_manager.get_prompts_config()

        # Override provider from CLI if specified
        provider_type = provider or llm_config.get("provider", "mock")
        logger.info(f"Using LLM provider: {provider_type}")

        # Create LLM provider
        provider_config = {
            "model": llm_config.get("model"),
            "temperature": llm_config.get("temperature"),
            "max_tokens": llm_config.get("max_tokens"),
            "top_p": llm_config.get("top_p"),
        }
        retry_config = config_manager.get_retry_config()
        provider_config.update(retry_config)

        try:
            llm_provider = LLMProviderFactory.create(provider_type, provider_config)
        except ValueError as e:
            _die(logger, str(e), verbose=ctx.obj.get("verbose", False), exc=e)

        # Create validator if enabled
        validator = None
        if not no_validate and validator_config.get("enabled", False):
            validator_provider_type = validator_config.get("provider") or provider_type
            validator_model = validator_config.get("model")
            validator_threshold = validator_config.get("threshold", 0.7)

            validator_provider_config = provider_config.copy()
            if validator_model:
                validator_provider_config["model"] = validator_model

            try:
                validator_llm = LLMProviderFactory.create(
                    validator_provider_type, validator_provider_config
                )
            except ValueError as e:
                _die(logger, str(e), verbose=ctx.obj.get("verbose", False), exc=e)
            validator = CommentValidator(
                validator_llm,
                threshold=validator_threshold,
                custom_prompt=prompts_config.get("validation"),
            )
            logger.info("Comment validation enabled")

        # Parse file/diff
        file_change = parse_file_or_diff(file_path)

        # Create review service
        mode = (comments_mode or comments_config.get("mode", "both")).lower()
        review_service = ReviewService(
            llm_provider,
            validator=validator,
            custom_review_prompt=prompts_config.get("review"),
            comment_mode=mode,
            max_context_tokens=limits_config.get("max_context_tokens"),
            chunk_overlap_lines=limits_config.get("chunk_overlap_size", 200),
        )

        # Review file
        result = review_service.review_file(file_change)

        # Output results
        if result.error:
            click.echo(f"ERROR: {result.error}", err=True)
            sys.exit(1)

        if result.has_comments:
            click.echo(f"\nReview results for: {result.file_change.path}\n")
            click.echo("=" * 80)

            for comment in result.comments:
                click.echo(f"\n{comment.to_markdown()}\n")
                click.echo("-" * 80)

            if result.summary:
                click.echo(f"\nSummary:\n{result.summary}\n")
        else:
            click.echo("No issues found!")

        if validator:
            stats = validator.get_stats()
            click.echo(
                f"\nValidation stats: {stats['valid']}/{stats['total']} comments passed"
            )

        click.echo(f"\nReview completed: {len(result.comments)} comments generated")

    except click.ClickException:
        raise
    except Exception as e:
        _die(logger, f"Fatal error: {e}", verbose=ctx.obj.get("verbose", False), exc=e)


@cli.command()
@click.argument("project_id", type=str)
@click.argument("merge_request_iid", type=int)
@click.option(
    "--provider",
    type=str,
    help="LLM provider to use (mock, openrouter, openai, deepseek, vllm). Overrides config.",
)
@click.option(
    "--comments-mode",
    type=click.Choice(["inline", "summary", "both"], case_sensitive=False),
    help="Comment mode: inline, summary, both. Overrides config.",
)
@click.option(
    "--no-validate",
    is_flag=True,
    help="Disable comment validation",
)
@click.option(
    "--no-post",
    is_flag=True,
    help="Don't post comments to GitLab (dry run)",
)
@click.option(
    "--gitlab-url",
    type=str,
    help="GitLab instance URL (default: from GITLAB_URL env or gitlab.com)",
)
@click.pass_context
def mr(
    ctx,
    project_id: str,
    merge_request_iid: int,
    provider: str,
    comments_mode: str,
    no_validate: bool,
    no_post: bool,
    gitlab_url: str,
):
    """Review a GitLab merge request.
    
    PROJECT_ID: GitLab project ID or path (e.g., 'group/project')
    MERGE_REQUEST_IID: Merge request IID (internal ID, shown as !123)
    """
    logger = logging.getLogger(__name__)
    logger.info(f"Starting review of MR !{merge_request_iid} in {project_id}")

    try:
        config_manager = ConfigManager(config_path=ctx.obj.get("config_path"))
        llm_config = config_manager.get_llm_config()
        validator_config = config_manager.get_validator_config()
        ignore_config = config_manager.config.get("ignore", {})
        comments_config = config_manager.get_comments_config()
        limits_config = config_manager.get_limits_config()
        prompts_config = config_manager.get_prompts_config()

        # Override provider from CLI if specified
        provider_type = provider or llm_config.get("provider", "mock")
        logger.info(f"Using LLM provider: {provider_type}")

        # Create LLM provider
        provider_config = {
            "model": llm_config.get("model"),
            "temperature": llm_config.get("temperature"),
            "max_tokens": llm_config.get("max_tokens"),
            "top_p": llm_config.get("top_p"),
        }
        retry_config = config_manager.get_retry_config()
        provider_config.update(retry_config)

        try:
            llm_provider = LLMProviderFactory.create(provider_type, provider_config)
        except ValueError as e:
            _die(logger, str(e), verbose=ctx.obj.get("verbose", False), exc=e)

        # Create validator if enabled
        validator = None
        if not no_validate and validator_config.get("enabled", False):
            validator_provider_type = validator_config.get("provider") or provider_type
            validator_model = validator_config.get("model")
            validator_threshold = validator_config.get("threshold", 0.7)

            validator_provider_config = provider_config.copy()
            if validator_model:
                validator_provider_config["model"] = validator_model

            try:
                validator_llm = LLMProviderFactory.create(
                    validator_provider_type, validator_provider_config
                )
            except ValueError as e:
                _die(logger, str(e), verbose=ctx.obj.get("verbose", False), exc=e)
            validator = CommentValidator(
                validator_llm,
                threshold=validator_threshold,
                custom_prompt=prompts_config.get("validation"),
            )
            logger.info("Comment validation enabled")

        # Create GitLab client
        try:
            gitlab_client = GitLabClient(
                gitlab_url=gitlab_url,
                max_retries=retry_config.get("max_attempts", 3),
                retry_delay=retry_config.get("initial_delay", 1.0),
            )
        except ValueError as e:
            _die(logger, str(e), verbose=ctx.obj.get("verbose", False), exc=e)

        # Create file filter
        file_filter = FileFilter(
            ignore_patterns=ignore_config.get("patterns", []),
            ignore_binary=ignore_config.get("binary_files", True),
        )

        # Create review service
        mode = (comments_mode or comments_config.get("mode", "both")).lower()
        review_service = ReviewService(
            llm_provider,
            validator=validator,
            custom_review_prompt=prompts_config.get("review"),
            comment_mode=mode,
            max_context_tokens=limits_config.get("max_context_tokens"),
            chunk_overlap_lines=limits_config.get("chunk_overlap_size", 200),
        )

        # Create MR review service
        mr_review_service = MRReviewService(
            llm_provider=llm_provider,
            gitlab_client=gitlab_client,
            file_filter=file_filter,
            review_service=review_service,
            max_files=limits_config.get("max_files"),
            max_lines=limits_config.get("max_lines"),
            comment_mode=mode,
        )

        # Review MR
        stats = mr_review_service.review_merge_request(
            project_id=project_id,
            merge_request_iid=merge_request_iid,
            post_comments=not no_post,
        )

        # Output statistics
        click.echo("\n" + "=" * 80)
        click.echo("Review Statistics")
        click.echo("=" * 80)
        click.echo(f"Total files in MR: {stats['total_files']}")
        click.echo(f"Files after filtering: {stats['filtered_files']}")
        click.echo(f"Ignored files: {stats['ignored_files']}")
        click.echo(f"Files processed: {stats['processed_files']}")
        click.echo(f"Total comments generated: {stats['total_comments']}")
        if not no_post:
            click.echo(f"Comments posted to GitLab: {stats['comments_posted']}")
            if stats['comments_failed'] > 0:
                click.echo(f"Failed to post: {stats['comments_failed']}", err=True)
        else:
            click.echo("(Dry run - comments not posted)")

        click.echo("\nReview completed!")

    except click.ClickException:
        raise
    except Exception as e:
        _die(logger, f"Fatal error: {e}", verbose=ctx.obj.get("verbose", False), exc=e)


def main():
    """Main entry point"""
    cli(obj={})


if __name__ == "__main__":
    main()
