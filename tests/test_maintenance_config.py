"""Tests for maintenance config fields."""

from __future__ import annotations

import pytest

from trw_mcp.models.config import TRWConfig, _reset_config


class TestTRWConfigMaintenanceFields:
    """Validate new config fields introduced for maintenance features."""

    def test_run_auto_close_enabled_default(self) -> None:
        cfg = TRWConfig()
        assert cfg.run_auto_close_enabled is True

    def test_run_auto_close_age_days_default(self) -> None:
        cfg = TRWConfig()
        assert cfg.run_auto_close_age_days == 7

    def test_learning_auto_prune_on_deliver_default(self) -> None:
        cfg = TRWConfig()
        assert cfg.learning_auto_prune_on_deliver is True

    def test_learning_auto_prune_cap_default(self) -> None:
        cfg = TRWConfig()
        assert cfg.learning_auto_prune_cap == 150

    def test_run_auto_close_enabled_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_RUN_AUTO_CLOSE_ENABLED", "false")
        _reset_config()
        cfg = TRWConfig()
        assert cfg.run_auto_close_enabled is False

    def test_run_auto_close_age_days_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_RUN_AUTO_CLOSE_AGE_DAYS", "14")
        _reset_config()
        cfg = TRWConfig()
        assert cfg.run_auto_close_age_days == 14

    def test_learning_auto_prune_on_deliver_env_override(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("TRW_LEARNING_AUTO_PRUNE_ON_DELIVER", "false")
        _reset_config()
        cfg = TRWConfig()
        assert cfg.learning_auto_prune_on_deliver is False

    def test_learning_auto_prune_cap_env_override(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("TRW_LEARNING_AUTO_PRUNE_CAP", "200")
        _reset_config()
        cfg = TRWConfig()
        assert cfg.learning_auto_prune_cap == 200
