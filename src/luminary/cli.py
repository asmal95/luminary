"""CLI interface for Luminary"""

import logging
import sys
from pathlib import Path
from typing import Optional

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

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """Setup logging configuration"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )
    logging.getLogger().setLevel(level)
    for logger_name in logging.Logger.manager.loggerDict:
        logging.getLogger(logger_name).setLevel(level)


def _die(message: str, verbose: bool = False, exc: Optional[Exception] = None) -> None:
    """Exit with a user-friendly error message"""
    if exc is not None:
        logger.error(message, exc_info=verbose)
    else:
        logger.error(message)
    raise click.ClickException(message)


def parse_file_or_diff(file_path: Path) -> FileChange:
    """Parse a file or diff into FileChange object

    Args:
        file_path: Path to file or diff

    Returns:
        FileChange object
    """
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Check if it's a diff (starts with --- or +++)
    if content.startswith("--- ") or content.startswith("+++ "):
        return parse_unified_diff(content, str(file_path))
    return parse_file_content(file_path)


def _create_provider_config(config_manager: ConfigManager) -> dict:
    """Create provider configuration dictionary

    Args:
        config_manager: Configuration manager

    Returns:
        Provider configuration dictionary
    """
    llm_config = config_manager.get_llm_config()
    retry_config = config_manager.get_retry_config()
    
    provider_config = {
        "model": llm_config.model,
        "temperature": llm_config.temperature,
        "max_tokens": llm_config.max_tokens,
        "top_p": llm_config.top_p,
    }
    # Add retry config fields
    provider_config.update(retry_config.model_dump())
    return provider_config


def _create_llm_provider(
    config_manager: ConfigManager,
    provider_override: Optional[str],
    verbose: bool,
) -> any:
    """Create LLM provider from config

    Args:
        config_manager: Configuration manager
        provider_override: Optional provider override from CLI
        verbose: Verbose mode for error reporting

    Returns:
        LLM provider instance
    """
    llm_config = config_manager.get_llm_config()
    provider_type = provider_override or llm_config.provider
    logger.info(f"Using LLM provider: {provider_type}")

    provider_config = _create_provider_config(config_manager)

    try:
        return LLMProviderFactory.create(provider_type, provider_config)
    except ValueError as e:
        _die(str(e), verbose=verbose, exc=e)


def _create_validator(
    config_manager: ConfigManager,
    llm_provider: any,
    provider_config: dict,
    no_validate: bool,
    verbose: bool,
) -> Optional[CommentValidator]:
    """Create comment validator if enabled

    Args:
        config_manager: Configuration manager
        llm_provider: Main LLM provider (used as fallback)
        provider_config: Provider configuration
        no_validate: Whether validation is disabled
        verbose: Verbose mode for error reporting

    Returns:
        CommentValidator instance or None
    """
    validator_config = config_manager.get_validator_config()
    if no_validate or not validator_config.enabled:
        return None

    validator_provider_type = validator_config.provider
    validator_model = validator_config.model
    validator_threshold = validator_config.threshold

    validator_provider_config = provider_config.copy()
    if validator_model:
        validator_provider_config["model"] = validator_model

    # Use same provider if no validator provider specified
    if validator_provider_type:
        try:
            validator_llm = LLMProviderFactory.create(
                validator_provider_type, validator_provider_config
            )
        except ValueError as e:
            _die(str(e), verbose=verbose, exc=e)
    else:
        validator_llm = llm_provider

    prompts_config = config_manager.get_prompts_config()
    validator = CommentValidator(
        validator_llm,
        threshold=validator_threshold,
        custom_prompt=prompts_config.validation,
    )
    logger.info("Comment validation enabled")
    return validator


def _create_review_service(
    config_manager: ConfigManager,
    llm_provider: any,
    validator: Optional[CommentValidator],
    comments_mode_override: Optional[str],
) -> ReviewService:
    """Create review service

    Args:
        config_manager: Configuration manager
        llm_provider: LLM provider
        validator: Optional comment validator
        comments_mode_override: Optional comments mode override

    Returns:
        ReviewService instance
    """
    comments_config = config_manager.get_comments_config()
    limits_config = config_manager.get_limits_config()
    prompts_config = config_manager.get_prompts_config()

    mode = (comments_mode_override or comments_config.mode).lower()
    return ReviewService(
        llm_provider,
        validator=validator,
        custom_review_prompt=prompts_config.review,
        comment_mode=mode,
        max_context_tokens=limits_config.max_context_tokens,
        chunk_overlap_lines=limits_config.chunk_overlap_size,
    )


def _output_file_review_results(result: any, validator: Optional[CommentValidator]) -> None:
    """Output file review results to console

    Args:
        result: Review result
        validator: Optional validator for stats
    """
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
        click.echo(f"\nValidation stats: {stats['valid']}/{stats['total']} comments passed")

    click.echo(f"\nReview completed: {len(result.comments)} comments generated")


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
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
@click.option("--no-validate", is_flag=True, help="Disable comment validation")
@click.pass_context
def file(ctx, file_path: Path, provider: str, comments_mode: str, no_validate: bool):
    """Review a single file using AI.

    FILE_PATH: Path to the file or diff to review
    """
    logger.info(f"Starting review for: {file_path}")
    verbose = ctx.obj.get("verbose", False)

    try:
        config_manager = ConfigManager(config_path=ctx.obj.get("config_path"))
        llm_provider = _create_llm_provider(config_manager, provider, verbose)
        provider_config = _create_provider_config(config_manager)

        validator = _create_validator(
            config_manager, llm_provider, provider_config, no_validate, verbose
        )
        review_service = _create_review_service(
            config_manager, llm_provider, validator, comments_mode
        )

        file_change = parse_file_or_diff(file_path)
        result = review_service.review_file(file_change)
        _output_file_review_results(result, validator)

    except click.ClickException:
        raise
    except Exception as e:
        _die(f"Fatal error: {e}", verbose=verbose, exc=e)


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
@click.option("--no-validate", is_flag=True, help="Disable comment validation")
@click.option("--no-post", is_flag=True, help="Don't post comments to GitLab (dry run)")
@click.option(
    "--gitlab-url",
    type=str,
    help="GitLab instance URL (default: from GITLAB_URL env or gitlab.com)",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose (DEBUG) logging")
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
    verbose: bool,
):
    """Review a GitLab merge request.

    PROJECT_ID: GitLab project ID or path (e.g., 'group/project')
    MERGE_REQUEST_IID: Merge request IID (internal ID, shown as !123)
    """
    verbose_mode = verbose or ctx.obj.get("verbose", False)
    if verbose_mode:
        setup_logging(verbose=True)

    logger.info(f"Starting review of MR !{merge_request_iid} in {project_id}")

    try:
        config_manager = ConfigManager(config_path=ctx.obj.get("config_path"))
        llm_provider = _create_llm_provider(config_manager, provider, verbose_mode)
        provider_config = _create_provider_config(config_manager)

        validator = _create_validator(
            config_manager, llm_provider, provider_config, no_validate, verbose_mode
        )
        review_service = _create_review_service(
            config_manager, llm_provider, validator, comments_mode
        )

        # Create GitLab client
        retry_config = config_manager.get_retry_config()
        try:
            from luminary.infrastructure.http_client import retry_config_from_dict

            retry_config_obj = retry_config_from_dict(retry_config)
            gitlab_client = GitLabClient(
                gitlab_url=gitlab_url,
                retry_config=retry_config_obj,
            )
        except ValueError as e:
            _die(str(e), verbose=verbose_mode, exc=e)

        # Create file filter
        ignore_config = config_manager.get_ignore_config()
        file_filter = FileFilter(
            ignore_patterns=ignore_config.patterns,
            ignore_binary=ignore_config.binary_files,
        )

        # Create MR review service
        limits_config = config_manager.get_limits_config()
        comments_config = config_manager.get_comments_config()
        mode = (comments_mode or comments_config.mode).lower()
        mr_review_service = MRReviewService(
            llm_provider=llm_provider,
            gitlab_client=gitlab_client,
            file_filter=file_filter,
            review_service=review_service,
            max_files=limits_config.max_files,
            max_lines=limits_config.max_lines,
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
            if stats["comments_failed"] > 0:
                click.echo(f"Failed to post: {stats['comments_failed']}", err=True)
        else:
            click.echo("(Dry run - comments not posted)")

        click.echo("\nReview completed!")

    except click.ClickException:
        raise
    except Exception as e:
        _die(f"Fatal error: {e}", verbose=verbose_mode, exc=e)


def main():
    """Main entry point"""
    cli(obj={})


if __name__ == "__main__":
    main()
