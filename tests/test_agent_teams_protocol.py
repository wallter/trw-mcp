"""Tests for retired beta team compatibility shims and related config/template behavior."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests._test_agent_teams_support import _PKG_DATA
from tests.conftest import get_tools_sync
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.claude_md import render_agent_teams_protocol, render_template

_CFG = TRWConfig()


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    return tmp_path


def _get_tools() -> dict[str, Any]:
    """Create fresh server and return tool map."""
    from fastmcp import FastMCP

    from trw_mcp.tools.learning import register_learning_tools

    srv = FastMCP("test")
    register_learning_tools(srv)
    return get_tools_sync(srv)


class TestRenderAgentTeamsProtocol:
    """Tests for the retired beta team compatibility shim."""

    @pytest.mark.parametrize("enabled", [True, False])
    def test_shim_is_empty_even_if_legacy_flag_is_set(self, enabled: bool) -> None:
        """v25 keeps the public symbol but emits no beta protocol body."""
        with patch(
            "trw_mcp.state.claude_md._static_sections.get_config",
            return_value=TRWConfig(agent_teams_enabled=enabled),
        ):
            result = render_agent_teams_protocol()

        assert result == ""


class TestAgentTeamsTemplateIntegration:
    """Tests for legacy template placeholder behavior."""

    def test_template_placeholder_replaced(self) -> None:
        """{{agent_teams_section}} placeholder is correctly replaced by supplied context."""
        template = "before\n{{agent_teams_section}}after"
        context = {"agent_teams_section": ""}
        result = render_template(template, context)
        assert "{{agent_teams_section}}" not in result
        assert result == "before\nafter"

    def test_bundled_template_has_placeholder_support(self) -> None:
        """Bundled claude_md.md template keeps compact placeholders for renderer compatibility."""
        bundled = _PKG_DATA / "templates" / "claude_md.md"
        assert bundled.exists(), "Bundled template must exist"
        content = bundled.read_text(encoding="utf-8")
        assert "{{imperative_opener}}" in content

    def test_full_sync_succeeds_without_beta_agent_teams(self, tmp_path: Path) -> None:
        """trw_claude_md_sync succeeds and omits retired beta team protocol content."""
        trw_dir = tmp_path / _CFG.trw_dir
        trw_dir.mkdir(parents=True, exist_ok=True)
        (trw_dir / _CFG.learnings_dir / _CFG.entries_dir).mkdir(parents=True, exist_ok=True)

        tools = _get_tools()
        with patch(
            "trw_mcp.state.claude_md._static_sections.get_config",
            return_value=TRWConfig(agent_teams_enabled=True),
        ):
            result = tools["trw_claude_md_sync"].fn(scope="root")

        assert result["status"] == "synced"
        content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert "trw:start" in content
        assert "beta team Protocol" not in content
        assert "TeamCreate" not in content


class TestAgentTeamsConfig:
    """Tests for agent_teams_enabled compatibility field."""

    def test_default_disabled(self) -> None:
        """agent_teams_enabled defaults to False in v25."""
        config = TRWConfig()
        assert config.agent_teams_enabled is False

    def test_env_override_still_parses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TRW_AGENT_TEAMS_ENABLED remains accepted for legacy config compatibility."""
        monkeypatch.setenv("TRW_AGENT_TEAMS_ENABLED", "true")
        config = TRWConfig()
        assert config.agent_teams_enabled is True
