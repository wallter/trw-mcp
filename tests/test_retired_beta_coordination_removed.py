"""Regression tests for removing the retired beta peer-team surface."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests._test_bundle_asset_support import _PKG_DATA
from tests.conftest import get_tools_sync
from trw_mcp.models.config import TRWConfig, resolve_client_profile
from trw_mcp.state.claude_md import render_template
from trw_mcp.state.claude_md._parser import load_claude_md_template

_CFG = TRWConfig()
_BETA_LABEL = "Agent " + "Teams"
_BETA_CREATE = "Team" + "Create"


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


class TestRetiredBetaRendererSurface:
    """The retired provider-specific renderer is gone, not shimmed."""

    def test_static_sections_no_longer_exports_beta_renderer(self) -> None:
        import trw_mcp.state.claude_md._static_sections as static_sections

        assert not hasattr(static_sections, "render_agent" + "_teams_protocol")

    def test_public_package_no_longer_exports_beta_renderer(self) -> None:
        import trw_mcp.state.claude_md as claude_md

        assert not hasattr(claude_md, "render_agent" + "_teams_protocol")


class TestRetiredBetaTemplateSurface:
    """Templates no longer carry the retired placeholder."""

    def test_inline_fallback_template_omits_retired_placeholder(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)
        template = load_claude_md_template(trw_dir)
        assert "agent" + "_teams_section" not in template

    def test_template_engine_still_blanks_unknown_project_placeholders(self) -> None:
        """Project-local old placeholders do not leak if a user still has one."""
        placeholder = "agent" + "_teams_section"
        result = render_template(f"before\n{{{{{placeholder}}}}}after", {})
        assert placeholder not in result
        assert result == "before\nafter"

    def test_bundled_template_has_compact_v25_placeholders(self) -> None:
        bundled = _PKG_DATA / "templates" / "claude_md.md"
        assert bundled.exists(), "Bundled template must exist"
        content = bundled.read_text(encoding="utf-8")
        assert "{{imperative_opener}}" in content
        assert "agent" + "_teams_section" not in content

    def test_full_sync_succeeds_without_retired_protocol(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / _CFG.trw_dir
        trw_dir.mkdir(parents=True, exist_ok=True)
        (trw_dir / _CFG.learnings_dir / _CFG.entries_dir).mkdir(parents=True, exist_ok=True)

        result = _get_tools()["trw_claude_md_sync"].fn(scope="root")

        assert result["status"] == "synced"
        content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert "trw:start" in content
        assert _BETA_LABEL not in content
        assert _BETA_CREATE not in content


class TestRetiredBetaConfigSurface:
    """The old compatibility config/profile names are fully removed."""

    def test_config_has_no_retired_flag(self) -> None:
        assert not hasattr(TRWConfig(), "agent" + "_teams_enabled")

    def test_env_override_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_AGENT" + "_TEAMS_ENABLED", "true")
        assert not hasattr(TRWConfig(), "agent" + "_teams_enabled")

    @pytest.mark.parametrize(
        "client_id", ["claude-code", "opencode", "cursor-ide", "cursor-cli", "codex", "copilot", "gemini", "aider"]
    )
    def test_profiles_have_no_retired_flag(self, client_id: str) -> None:
        profile = resolve_client_profile(client_id)
        assert not hasattr(profile, "include_agent" + "_teams")
