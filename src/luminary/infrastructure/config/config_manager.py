"""Configuration manager for loading and validating .ai-reviewer.yml"""

import copy
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from pydantic import ValidationError

from luminary.domain.config import (
    AppConfig,
    CommentsConfig,
    GitLabConfig,
    IgnoreConfig,
    LimitsConfig,
    LLMConfig,
    PromptsConfig,
    RetryConfig,
    ValidatorConfig,
)

logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    """Configuration validation error."""

    pass


class ConfigManager:
    """Manages configuration from .ai-reviewer.yml and environment variables
    
    Loads configuration with validation using Pydantic models. Configuration priority:
    1. Default values (defined in Pydantic models)
    2. .ai-reviewer.yml file (searched from current directory)
    3. Environment variables (LUMINARY_*, GITLAB_*)
    4. CLI arguments (handled by CLI layer)
    """

    # Legacy DEFAULT_CONFIG for backward compatibility
    DEFAULT_CONFIG = {
        "llm": {
            "provider": "mock",
            "model": "anthropic/claude-3.5-sonnet",
            "temperature": 0.7,
            "max_tokens": 2000,
            "top_p": 0.9,
        },
        "validator": {
            "enabled": False,
            "provider": None,
            "model": None,
            "threshold": 0.7,
        },
        "gitlab": {
            "url": None,
            "token": None,
        },
        "ignore": {
            "patterns": [
                "*.lock",
                "*.min.js",
                "*.min.css",
                "*.map",
                "node_modules/**",
                ".git/**",
            ],
            "binary_files": True,
        },
        "limits": {
            "max_files": None,
            "max_lines": None,
            "max_context_tokens": None,
            "chunk_overlap_size": 200,
        },
        "comments": {
            "mode": "both",
            "severity_levels": True,
            "markdown": True,
        },
        "prompts": {
            "review": None,
            "validation": None,
        },
        "retry": {
            "max_attempts": 3,
            "backoff_multiplier": 2,
            "initial_delay": 1,
            "jitter": 0.1,
        },
    }

    def __init__(self, config_path: Optional[Path] = None):
        """Initialize config manager
        
        Args:
            config_path: Path to .ai-reviewer.yml (searches from current dir if None)
            
        Raises:
            ConfigurationError: If configuration validation fails
        """
        # Convert to Path if string
        if isinstance(config_path, str):
            config_path = Path(config_path)
        self.config_path = config_path or self._find_config_file()
        try:
            self.config: AppConfig = self._load_config()
        except ValidationError as e:
            # Format validation errors for user
            errors = []
            for error in e.errors():
                field = ".".join(str(x) for x in error["loc"])
                msg = error["msg"]
                errors.append(f"  - {field}: {msg}")
            raise ConfigurationError(
                f"Configuration validation failed:\n" + "\n".join(errors)
            ) from e

    def _find_config_file(self) -> Optional[Path]:
        """Find .ai-reviewer.yml file starting from current directory
        
        Returns:
            Path to config file or None if not found
        """
        current = Path.cwd()
        for parent in [current] + list(current.parents):
            config_file = parent / ".ai-reviewer.yml"
            if config_file.exists():
                logger.info(f"Found config file: {config_file}")
                return config_file
        logger.debug("No .ai-reviewer.yml found, using defaults")
        return None

    def _load_config(self) -> AppConfig:
        """Load configuration from file and validate with Pydantic
        
        Returns:
            Validated AppConfig instance
            
        Raises:
            ValidationError: If configuration is invalid
        """
        config_dict = copy.deepcopy(self.DEFAULT_CONFIG)

        if self.config_path and self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    file_config = yaml.safe_load(f) or {}
                config_dict = self._merge_config(config_dict, file_config)
                logger.info(f"Loaded configuration from {self.config_path}")
            except Exception as e:
                logger.warning(f"Failed to load config from {self.config_path}: {e}")
                logger.info("Using default configuration")

        # Override with environment variables
        config_dict = self._apply_env_overrides(config_dict)

        # Validate and create AppConfig
        return AppConfig(**config_dict)

    def _merge_config(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively merge configuration dictionaries
        
        Args:
            base: Base configuration
            override: Override configuration
            
        Returns:
            Merged configuration
        """
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge_config(result[key], value)
            else:
                result[key] = value
        return result

    def _apply_env_overrides(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Apply environment variable overrides
        
        Args:
            config: Configuration dictionary
            
        Returns:
            Configuration with env overrides applied
        """
        # LLM provider
        if os.getenv("LUMINARY_LLM_PROVIDER"):
            config["llm"]["provider"] = os.getenv("LUMINARY_LLM_PROVIDER")

        # LLM model
        if os.getenv("LUMINARY_LLM_MODEL"):
            config["llm"]["model"] = os.getenv("LUMINARY_LLM_MODEL")

        # API keys are handled by providers themselves
        return config

    def get_llm_config(self) -> LLMConfig:
        """Get LLM provider configuration
        
        Returns:
            LLM configuration model
        """
        return self.config.llm

    def get_validator_config(self) -> ValidatorConfig:
        """Get validator configuration
        
        Returns:
            Validator configuration model
        """
        return self.config.validator

    def get_ignore_config(self) -> IgnoreConfig:
        """Get ignore configuration
        
        Returns:
            Ignore configuration model
        """
        return self.config.ignore

    def get_ignore_patterns(self) -> list:
        """Get ignore patterns
        
        Returns:
            List of ignore patterns
        """
        return self.config.ignore.patterns

    def should_ignore_binary_files(self) -> bool:
        """Check if binary files should be ignored
        
        Returns:
            True if binary files should be ignored
        """
        return self.config.ignore.binary_files

    def get_retry_config(self) -> RetryConfig:
        """Get retry configuration
        
        Returns:
            Retry configuration model
        """
        return self.config.retry

    def get_limits_config(self) -> LimitsConfig:
        """Get limits configuration
        
        Returns:
            Limits configuration model
        """
        return self.config.limits

    def get_comments_config(self) -> CommentsConfig:
        """Get comments configuration
        
        Returns:
            Comments configuration model
        """
        return self.config.comments

    def get_prompts_config(self) -> PromptsConfig:
        """Get prompts configuration
        
        Returns:
            Prompts configuration model
        """
        return self.config.prompts

    def get_gitlab_config(self) -> GitLabConfig:
        """Get GitLab configuration
        
        Returns:
            GitLab configuration model
        """
        return self.config.gitlab

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by key (supports dot notation)
        
        Args:
            key: Configuration key (e.g., "llm.model" or "llm")
            default: Default value if key not found
            
        Returns:
            Configuration value or default
        """
        keys = key.split(".")
        value = self.config.model_dump()
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value
