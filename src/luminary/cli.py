"""CLI interface for Luminary"""

import logging
import sys
from pathlib import Path

import click

from luminary.application.review_service import ReviewService
from luminary.domain.models.file_change import FileChange
from luminary.infrastructure.diff_parser import parse_file_content, parse_unified_diff
from luminary.infrastructure.llm.mock import MockLLMProvider


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
    "--mock-delay",
    type=float,
    default=0.1,
    help="Mock LLM delay in seconds (default: 0.1)",
)
def main(file_path: Path, verbose: bool, mock_delay: float):
    """Luminary - AI Code Reviewer for GitLab
    
    Review a single file using AI.
    
    FILE_PATH: Path to the file or diff to review
    """
    setup_logging(verbose)

    logger = logging.getLogger(__name__)
    logger.info(f"Starting review for: {file_path}")

    try:
        # Parse file/diff
        file_change = parse_file_or_diff(file_path)

        # Initialize mock LLM provider
        mock_provider = MockLLMProvider(config={"delay": mock_delay})

        # Create review service
        review_service = ReviewService(mock_provider)

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

        click.echo(f"\nReview completed: {len(result.comments)} comments generated")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        click.echo(f"❌ Fatal error: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
