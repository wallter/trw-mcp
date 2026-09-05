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
        """PRD-FIX-124-FR06: recalibrated from 0.7, which was unreachable.

        0.7 was chosen as if the score were a probability. It is an IDF-weighted
        prompt-coverage fraction, and at 0.7 the limb fired 0/20 in-domain
        prompts on the live store. See
        docs/documentation/operational-knowledge/auto-recall-calibration.md.
        """
        from trw_mcp.models.config import TRWConfig

        config = TRWConfig()
        assert config.auto_recall_min_score == 0.35

    def test_auto_recall_scan_cap_default(self) -> None:
        """PRD-FIX-124-FR07: promoted from a magic 500 inside the hook."""
        from trw_mcp.models.config import TRWConfig

        config = TRWConfig()
        assert config.auto_recall_scan_cap == 10000

    def test_auto_recall_scan_cap_env_override(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """TRW_AUTO_RECALL_SCAN_CAP env var overrides default."""
        from trw_mcp.models.config import TRWConfig

        monkeypatch.setenv("TRW_AUTO_RECALL_SCAN_CAP", "250")
        config = TRWConfig()
        assert config.auto_recall_scan_cap == 250

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
