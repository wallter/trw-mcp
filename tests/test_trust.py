"""Tests for Progressive Trust Model (PRD-CORE-068)."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.trust import (
    read_trust_registry,
    write_trust_registry,
)


@pytest.fixture()
def trust_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up a .trw directory with trust registry support."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "logs").mkdir(parents=True)

    config = TRWConfig()
    _reset_config(config)
    monkeypatch.setenv("TRW_AGENT_ID", "test-agent")

    yield trw_dir, config
    _reset_config()


class TestTrustRegistry:
    """FR01: Trust Registry Data Store."""

    def test_registry_created_on_first_access(self, trust_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, _ = trust_env
        registry = read_trust_registry(trw_dir)
        project = registry["project"]
        assert isinstance(project, dict)
        assert project["session_count"] == 0
        assert project["tier"] == "crawl"

    def test_registry_read_existing(self, trust_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, _ = trust_env
        write_trust_registry(
            trw_dir,
            {
                "project": {
                    "session_count": 75,
                    "successful_sessions": 72,
                    "last_session_at": "2026-03-01T18:00:00Z",
                    "tier": "walk",
                }
            },
        )
        registry = read_trust_registry(trw_dir)
        project = registry["project"]
        assert isinstance(project, dict)
        assert project["session_count"] == 75
