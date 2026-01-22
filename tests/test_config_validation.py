"""Tests for configuration validation with Pydantic."""

import tempfile
from pathlib import Path

import pytest
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
from luminary.infrastructure.config.config_manager import ConfigManager, ConfigurationError


class TestLLMConfigValidation:
    """Tests for LLMConfig validation."""

    def test_valid_llm_config(self):
        """Test valid LLM configuration"""
        config = LLMConfig(
            provider="openrouter",
            model="anthropic/claude-3.5-sonnet",
            temperature=0.7,
            max_tokens=2000,
            top_p=0.9,
        )
        assert config.provider == "openrouter"
        assert config.temperature == 0.7

    def test_invalid_provider(self):
        """Test invalid provider name"""
        with pytest.raises(ValidationError, match="provider"):
            LLMConfig(provider="invalid_provider")

    def test_temperature_too_high(self):
        """Test temperature above maximum"""
        with pytest.raises(ValidationError, match="temperature"):
            LLMConfig(temperature=3.0)

    def test_temperature_negative(self):
        """Test negative temperature"""
        with pytest.raises(ValidationError, match="temperature"):
            LLMConfig(temperature=-0.1)

    def test_max_tokens_zero(self):
        """Test max_tokens must be positive"""
        with pytest.raises(ValidationError, match="max_tokens"):
            LLMConfig(max_tokens=0)

    def test_top_p_above_one(self):
        """Test top_p above 1.0"""
        with pytest.raises(ValidationError, match="top_p"):
            LLMConfig(top_p=1.5)


class TestValidatorConfigValidation:
    """Tests for ValidatorConfig validation."""

    def test_valid_validator_config(self):
        """Test valid validator configuration"""
        config = ValidatorConfig(
            enabled=True,
            provider="openrouter",
            model="anthropic/claude-3-haiku",
            threshold=0.7,
        )
        assert config.enabled is True
        assert config.threshold == 0.7

    def test_threshold_above_one(self):
        """Test threshold above 1.0"""
        with pytest.raises(ValidationError, match="threshold"):
            ValidatorConfig(threshold=1.5)

    def test_threshold_negative(self):
        """Test negative threshold"""
        with pytest.raises(ValidationError, match="threshold"):
            ValidatorConfig(threshold=-0.1)


class TestRetryConfigValidation:
    """Tests for RetryConfig validation."""

    def test_valid_retry_config(self):
        """Test valid retry configuration"""
        config = RetryConfig(
            max_attempts=3,
            initial_delay=1.0,
            backoff_multiplier=2.0,
            jitter=0.1,
        )
        assert config.max_attempts == 3
        assert config.jitter == 0.1

    def test_max_attempts_zero(self):
        """Test max_attempts must be positive"""
        with pytest.raises(ValidationError, match="max_attempts"):
            RetryConfig(max_attempts=0)

    def test_max_attempts_too_high(self):
        """Test max_attempts above limit"""
        with pytest.raises(ValidationError, match="max_attempts"):
            RetryConfig(max_attempts=11)

    def test_backoff_multiplier_too_low(self):
        """Test backoff_multiplier below 1.0"""
        with pytest.raises(ValidationError, match="backoff_multiplier"):
            RetryConfig(backoff_multiplier=0.5)

    def test_jitter_above_one(self):
        """Test jitter above 1.0"""
        with pytest.raises(ValidationError, match="jitter"):
            RetryConfig(jitter=1.5)


class TestLimitsConfigValidation:
    """Tests for LimitsConfig validation."""

    def test_valid_limits_config(self):
        """Test valid limits configuration"""
        config = LimitsConfig(
            max_files=50,
            max_lines=10000,
            max_context_tokens=8000,
            chunk_overlap_size=200,
        )
        assert config.max_files == 50
        assert config.chunk_overlap_size == 200

    def test_max_files_zero(self):
        """Test max_files must be positive if set"""
        with pytest.raises(ValidationError, match="max_files"):
            LimitsConfig(max_files=0)

    def test_chunk_overlap_size_zero(self):
        """Test chunk_overlap_size must be positive"""
        with pytest.raises(ValidationError, match="chunk_overlap_size"):
            LimitsConfig(chunk_overlap_size=0)

    def test_none_values_allowed(self):
        """Test None values are allowed for optional limits"""
        config = LimitsConfig(max_files=None, max_lines=None, max_context_tokens=None)
        assert config.max_files is None
        assert config.max_lines is None


class TestCommentsConfigValidation:
    """Tests for CommentsConfig validation."""

    def test_valid_comments_config(self):
        """Test valid comments configuration"""
        config = CommentsConfig(
            mode="both",
            severity_levels=True,
            markdown=True,
        )
        assert config.mode == "both"

    def test_invalid_mode(self):
        """Test invalid comment mode"""
        with pytest.raises(ValidationError, match="mode"):
            CommentsConfig(mode="invalid")


class TestAppConfigValidation:
    """Tests for AppConfig validation."""

    def test_valid_app_config(self):
        """Test valid application configuration"""
        config = AppConfig()
        assert config.llm.provider == "mock"
        assert config.comments.mode == "both"

    def test_unknown_field_rejected(self):
        """Test unknown fields are rejected"""
        with pytest.raises(ValidationError, match="extra"):
            AppConfig(unknown_field="value")

    def test_nested_validation(self):
        """Test nested validation works"""
        with pytest.raises(ValidationError, match="temperature"):
            AppConfig(llm={"temperature": 5.0})


class TestConfigManagerValidation:
    """Tests for ConfigManager validation."""

    def test_load_valid_config_from_file(self):
        """Test loading valid configuration from file"""
        config_data = {
            "llm": {
                "provider": "openrouter",
                "model": "anthropic/claude-3.5-sonnet",
                "temperature": 0.7,
            }
        }
        
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(config_data, f)
            config_path = f.name

        try:
            manager = ConfigManager(config_path=config_path)
            assert manager.config.llm.provider == "openrouter"
        finally:
            Path(config_path).unlink()

    def test_load_invalid_config_raises_error(self):
        """Test loading invalid configuration raises error"""
        config_data = {
            "llm": {
                "temperature": 5.0,  # Invalid: above maximum
            }
        }
        
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(config_data, f)
            config_path = f.name

        try:
            with pytest.raises(ConfigurationError, match="temperature"):
                ConfigManager(config_path=config_path)
        finally:
            Path(config_path).unlink()

    def test_default_config_is_valid(self):
        """Test default configuration is valid"""
        manager = ConfigManager()
        assert manager.config.llm.provider == "mock"
        assert isinstance(manager.config, AppConfig)

    def test_get_typed_config_sections(self):
        """Test getter methods return typed models"""
        manager = ConfigManager()
        
        assert isinstance(manager.get_llm_config(), LLMConfig)
        assert isinstance(manager.get_validator_config(), ValidatorConfig)
        assert isinstance(manager.get_gitlab_config(), GitLabConfig)
        assert isinstance(manager.get_ignore_config(), IgnoreConfig)
        assert isinstance(manager.get_limits_config(), LimitsConfig)
        assert isinstance(manager.get_comments_config(), CommentsConfig)
        assert isinstance(manager.get_prompts_config(), PromptsConfig)
        assert isinstance(manager.get_retry_config(), RetryConfig)

    def test_env_overrides_work(self, monkeypatch):
        """Test environment variable overrides"""
        monkeypatch.setenv("LUMINARY_LLM_PROVIDER", "openai")
        monkeypatch.setenv("LUMINARY_LLM_MODEL", "gpt-4")
        
        manager = ConfigManager()
        assert manager.config.llm.provider == "openai"
        assert manager.config.llm.model == "gpt-4"


class TestIgnoreConfig:
    """Tests for IgnoreConfig."""

    def test_default_patterns(self):
        """Test default ignore patterns are set"""
        config = IgnoreConfig()
        assert "*.lock" in config.patterns
        assert config.binary_files is True

    def test_custom_patterns(self):
        """Test custom patterns"""
        config = IgnoreConfig(
            patterns=["*.test", "target/**"],
            binary_files=False,
        )
        assert "*.test" in config.patterns
        assert config.binary_files is False
