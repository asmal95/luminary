"""Main application configuration model."""

from pydantic import BaseModel, ConfigDict, Field

from luminary.domain.config.comments import CommentsConfig
from luminary.domain.config.gitlab import GitLabConfig
from luminary.domain.config.ignore import IgnoreConfig
from luminary.domain.config.limits import LimitsConfig
from luminary.domain.config.llm import LLMConfig
from luminary.domain.config.prompts import PromptsConfig
from luminary.domain.config.retry import RetryConfig
from luminary.domain.config.validator import ValidatorConfig


class AppConfig(BaseModel):
    """Main application configuration.

    This is the root configuration model that aggregates all configuration sections.
    Validation is performed at load time to fail fast on configuration errors.

    Attributes:
        llm: LLM provider configuration
        validator: Comment validation configuration
        gitlab: GitLab integration configuration
        ignore: File filtering configuration
        limits: Processing limits configuration
        comments: Comment generation configuration
        prompts: Custom prompts configuration
        retry: Retry logic configuration
    """

    llm: LLMConfig = Field(default_factory=LLMConfig)
    validator: ValidatorConfig = Field(default_factory=ValidatorConfig)
    gitlab: GitLabConfig = Field(default_factory=GitLabConfig)
    ignore: IgnoreConfig = Field(default_factory=IgnoreConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    comments: CommentsConfig = Field(default_factory=CommentsConfig)
    prompts: PromptsConfig = Field(default_factory=PromptsConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)

    model_config = ConfigDict(
        validate_assignment=True,  # Validate on attribute assignment
        extra="forbid",  # Reject unknown fields
        use_enum_values=True,  # Use enum values instead of enum objects
        json_schema_extra={
            "example": {
                "llm": {
                    "provider": "openrouter",
                    "model": "anthropic/claude-3.5-sonnet",
                    "temperature": 0.7,
                    "max_tokens": 2000,
                    "top_p": 0.9,
                },
                "validator": {
                    "enabled": True,
                    "provider": "openrouter",
                    "model": "anthropic/claude-3-haiku",
                    "threshold": 0.7,
                },
                "gitlab": {
                    "url": None,
                    "token": None,
                },
                "ignore": {
                    "patterns": ["*.lock", "*.min.js", "target/**"],
                },
                "limits": {
                    "max_files": 50,
                    "max_lines": 10000,
                    "max_context_tokens": 8000,
                    "chunk_overlap_size": 200,
                },
                "comments": {
                    "mode": "both",
                },
                "prompts": {
                    "review": None,
                    "validation": None,
                },
                "retry": {
                    "max_attempts": 3,
                    "initial_delay": 1.0,
                    "backoff_multiplier": 2.0,
                    "jitter": 0.1,
                },
            }
        },
    )
