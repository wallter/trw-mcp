"""Tests for TRWConfig sync fields — PRD-INFRA-051-FR07."""

from __future__ import annotations


def test_config_backend_url_default_empty() -> None:
    """FR07: backend_url defaults to empty string (offline mode)."""
    from trw_mcp.models.config._main import TRWConfig

    config = TRWConfig()
    assert config.backend_url == ""


def test_config_sync_interval_default() -> None:
    """FR07: sync_interval_seconds defaults to 300."""
    from trw_mcp.models.config._main import TRWConfig

    config = TRWConfig()
    assert config.sync_interval_seconds == 300


def test_config_sync_push_batch_size_default() -> None:
    """FR07: sync_push_batch_size defaults to 100."""
    from trw_mcp.models.config._main import TRWConfig

    config = TRWConfig()
    assert config.sync_push_batch_size == 100


def test_config_meta_tune_disabled_by_default() -> None:
    """FR07: meta_tune_enabled defaults to False."""
    from trw_mcp.models.config._main import TRWConfig

    config = TRWConfig()
    assert config.meta_tune_enabled is False


def test_config_team_sync_disabled_by_default() -> None:
    """FR07: team_sync_enabled defaults to False."""
    from trw_mcp.models.config._main import TRWConfig

    config = TRWConfig()
    assert config.team_sync_enabled is False


def test_config_sync_push_timeout_default() -> None:
    """FR07: sync_push_timeout_seconds defaults to 10.0."""
    from trw_mcp.models.config._main import TRWConfig

    config = TRWConfig()
    assert config.sync_push_timeout_seconds == 10.0


def test_config_backend_api_key_default_empty() -> None:
    """FR07: backend_api_key defaults to empty string."""
    from trw_mcp.models.config._main import TRWConfig

    config = TRWConfig()
    assert config.backend_api_key == ""


def test_config_sync_fields_load_from_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """FR07: sync fields honor TRW_ environment overrides."""
    from trw_mcp.models.config._main import TRWConfig

    monkeypatch.setenv("TRW_BACKEND_URL", "https://backend.example.com")
    monkeypatch.setenv("TRW_SYNC_INTERVAL_SECONDS", "120")

    config = TRWConfig()

    assert config.backend_url == "https://backend.example.com"
    assert config.sync_interval_seconds == 120
