"""Configuration-field tests for auto-recall."""

from __future__ import annotations

import pytest


class TestAutoRecallConfigFields:
    """Config fields for auto-recall exist and have correct defaults."""

    def test_auto_recall_enabled_default(self) -> None:
        from trw_mcp.models.config import TRWConfig

        config = TRWConfig()
        assert config.auto_recall_enabled is True

    def test_auto_recall_max_results_default(self) -> None:
        from trw_mcp.models.config import TRWConfig

        config = TRWConfig()
        assert config.auto_recall_max_results == 3

    def test_auto_recall_max_tokens_default(self) -> None:
        from trw_mcp.models.config import TRWConfig

        config = TRWConfig()
        assert config.auto_recall_max_tokens == 100

    def test_auto_recall_min_score_default(self) -> None:
        from trw_mcp.models.config import TRWConfig

        config = TRWConfig()
        assert config.auto_recall_min_score == 0.7

    def test_auto_recall_enabled_env_override(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """TRW_AUTO_RECALL_ENABLED env var can disable auto-recall."""
        from trw_mcp.models.config import TRWConfig

        monkeypatch.setenv("TRW_AUTO_RECALL_ENABLED", "false")
        config = TRWConfig()
        assert config.auto_recall_enabled is False

    def test_auto_recall_max_results_env_override(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """TRW_AUTO_RECALL_MAX_RESULTS env var overrides default."""
        from trw_mcp.models.config import TRWConfig

        monkeypatch.setenv("TRW_AUTO_RECALL_MAX_RESULTS", "10")
        config = TRWConfig()
        assert config.auto_recall_max_results == 10

    def test_auto_recall_max_tokens_env_override(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """TRW_AUTO_RECALL_MAX_TOKENS env var overrides default."""
        from trw_mcp.models.config import TRWConfig

        monkeypatch.setenv("TRW_AUTO_RECALL_MAX_TOKENS", "42")
        config = TRWConfig()
        assert config.auto_recall_max_tokens == 42

    def test_auto_recall_min_score_env_override(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """TRW_AUTO_RECALL_MIN_SCORE env var overrides default."""
        from trw_mcp.models.config import TRWConfig

        monkeypatch.setenv("TRW_AUTO_RECALL_MIN_SCORE", "0.9")
        config = TRWConfig()
        assert config.auto_recall_min_score == 0.9
