"""Config field tests for usage logging."""

from __future__ import annotations

import pytest

from trw_mcp.models.config import TRWConfig


class TestConfigUsageFields:
    """Test TRWConfig fields added for PRD-CORE-020."""

    def test_config_usage_fields(self) -> None:
        """TRWConfig has llm_usage_log_enabled=True and llm_usage_log_file='llm_usage.jsonl'."""
        config = TRWConfig()
        assert config.llm_usage_log_enabled is True
        assert config.llm_usage_log_file == "llm_usage.jsonl"

    def test_config_usage_log_enabled_default_true(self) -> None:
        """llm_usage_log_enabled defaults to True."""
        config = TRWConfig()
        assert config.llm_usage_log_enabled is True

    def test_config_usage_log_file_default(self) -> None:
        """llm_usage_log_file defaults to 'llm_usage.jsonl'."""
        config = TRWConfig()
        assert config.llm_usage_log_file == "llm_usage.jsonl"

    def test_config_usage_log_enabled_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """llm_usage_log_enabled can be overridden via env var."""
        monkeypatch.setenv("TRW_LLM_USAGE_LOG_ENABLED", "false")
        config = TRWConfig()
        assert config.llm_usage_log_enabled is False

    def test_config_usage_log_file_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """llm_usage_log_file can be overridden via env var."""
        monkeypatch.setenv("TRW_LLM_USAGE_LOG_FILE", "custom_usage.jsonl")
        config = TRWConfig()
        assert config.llm_usage_log_file == "custom_usage.jsonl"
