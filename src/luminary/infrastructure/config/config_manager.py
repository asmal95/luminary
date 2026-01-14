"""Configuration manager for loading and validating .ai-reviewer.yml"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

logger = logging.getLogger(__name__)


class ConfigManager:
    """Manages configuration from .ai-reviewer.yml and environment variables"""

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
            "provider": None,  # Uses same as llm if None
            "model": None,
            "threshold": 0.7,
        },
        "gitlab": {
            "url": None,  # None = from GITLAB_URL env or gitlab.com
            "token": None,  # None = from GITLAB_TOKEN env
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
        },
        "comments": {
            "mode": "both",  # inline, summary, both
            "severity_levels": True,
            "markdown": True,
        },
        "retry": {
            "max_attempts": 3,
            "backoff_multiplier": 2,
            "initial_delay": 1,
        },
    }

    def __init__(self, config_path: Optional[Path] = None):
        """Initialize config manager
        
        Args:
            config_path: Path to .ai-reviewer.yml (searches from current dir if None)
        """
        self.config_path = config_path or self._find_config_file()
        self.config = self._load_config()

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

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from file and merge with defaults
        
        Returns:
            Merged configuration dictionary
        """
        config = self.DEFAULT_CONFIG.copy()

        if self.config_path and self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    file_config = yaml.safe_load(f) or {}
                config = self._merge_config(config, file_config)
                logger.info(f"Loaded configuration from {self.config_path}")
            except Exception as e:
                logger.warning(f"Failed to load config from {self.config_path}: {e}")
                logger.info("Using default configuration")

        # Override with environment variables
        config = self._apply_env_overrides(config)

        return config

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

    def get_llm_config(self) -> Dict[str, Any]:
        """Get LLM provider configuration
        
        Returns:
            LLM configuration dictionary
        """
        return self.config.get("llm", {}).copy()

    def get_validator_config(self) -> Dict[str, Any]:
        """Get validator configuration
        
        Returns:
            Validator configuration dictionary
        """
        return self.config.get("validator", {}).copy()

    def get_ignore_patterns(self) -> list:
        """Get ignore patterns
        
        Returns:
            List of ignore patterns
        """
        return self.config.get("ignore", {}).get("patterns", [])

    def should_ignore_binary_files(self) -> bool:
        """Check if binary files should be ignored
        
        Returns:
            True if binary files should be ignored
        """
        return self.config.get("ignore", {}).get("binary_files", True)

    def get_retry_config(self) -> Dict[str, Any]:
        """Get retry configuration
        
        Returns:
            Retry configuration dictionary
        """
        return self.config.get("retry", {}).copy()

    def get_gitlab_config(self) -> Dict[str, Any]:
        """Get GitLab configuration
        
        Returns:
            GitLab configuration dictionary
        """
        return self.config.get("gitlab", {}).copy()

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by key (supports dot notation)
        
        Args:
            key: Configuration key (e.g., "llm.model" or "llm")
            default: Default value if key not found
            
        Returns:
            Configuration value or default
        """
        keys = key.split(".")
        value = self.config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value
