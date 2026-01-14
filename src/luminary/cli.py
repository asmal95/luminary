"""CLI interface for Luminary"""

import logging
import sys
from pathlib import Path

import click

from luminary.application.review_service import ReviewService
from luminary.domain.models.file_change import FileChange
from luminary.domain.validators.comment_validator import CommentValidator
from luminary.infrastructure.config.config_manager import ConfigManager
from luminary.infrastructure.diff_parser import parse_file_content, parse_unified_diff
from luminary.infrastructure.llm.factory import LLMProviderFactory


def setup_logging(verbose: bool = False):
    """Setup logging configuration"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


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


@click.command()
@click.argument("file_path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--verbose", "-v", is_flag=True, help="Enable verbose logging"
)
@click.option(
    "--provider",
    type=str,
    help="LLM provider to use (mock, openrouter). Overrides config.",
)
@click.option(
    "--no-validate",
    is_flag=True,
    help="Disable comment validation",
)
@click.option(
    "--config",
    type=click.Path(exists=True, path_type=Path),
    help="Path to .ai-reviewer.yml config file",
)
def main(
    file_path: Path,
    verbose: bool,
    provider: str,
    no_validate: bool,
    config: Path,
):
    """Luminary - AI Code Reviewer for GitLab
    
    Review a single file using AI.
    
    FILE_PATH: Path to the file or diff to review
    """
    setup_logging(verbose)

    logger = logging.getLogger(__name__)
    logger.info(f"Starting review for: {file_path}")

    try:
        # Load configuration
        config_manager = ConfigManager(config_path=config)
        llm_config = config_manager.get_llm_config()
        validator_config = config_manager.get_validator_config()

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
        # Add retry config
        retry_config = config_manager.get_retry_config()
        provider_config.update(retry_config)

        llm_provider = LLMProviderFactory.create(provider_type, provider_config)

        # Create validator if enabled
        validator = None
        if not no_validate and validator_config.get("enabled", False):
            validator_provider_type = validator_config.get("provider") or provider_type
            validator_model = validator_config.get("model")
            validator_threshold = validator_config.get("threshold", 0.7)

            validator_provider_config = provider_config.copy()
            if validator_model:
                validator_provider_config["model"] = validator_model

            validator_llm = LLMProviderFactory.create(
                validator_provider_type, validator_provider_config
            )
            validator = CommentValidator(
                validator_llm,
                threshold=validator_threshold,
            )
            logger.info("Comment validation enabled")

        # Parse file/diff
        file_change = parse_file_or_diff(file_path)

        # Create review service
        review_service = ReviewService(
            llm_provider,
            validator=validator,
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

            # Output comments
            for comment in result.comments:
                click.echo(f"\n{comment.to_markdown()}\n")
                click.echo("-" * 80)

            # Output summary
            if result.summary:
                click.echo(f"\nSummary:\n{result.summary}\n")
        else:
            click.echo("No issues found!")

        # Output validation stats if validator was used
        if validator:
            stats = validator.get_stats()
            click.echo(
                f"\nValidation stats: {stats['valid']}/{stats['total']} comments passed"
            )

        click.echo(f"\nReview completed: {len(result.comments)} comments generated")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        click.echo(f"Fatal error: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
