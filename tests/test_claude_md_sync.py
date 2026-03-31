"""Tests for CLAUDE.md / AGENTS.md sync: FR13 instructions sync generalization.

Covers:
  - FR13: client parameter routes writes to CLAUDE.md and/or AGENTS.md
  - FR13: auto-detection via detect_ide() drives default behavior
  - FR13: backward compatibility — trw_claude_md_sync still writes CLAUDE.md
  - FR13: AGENTS.md uses same markers and identical TRW section content
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_mcp.state.claude_md._parser import TRW_MARKER_END, TRW_MARKER_START

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_sync_args(tmp_path: Path) -> dict[str, object]:
    """Build minimal args for execute_claude_md_sync using tmp_path as root."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.persistence import FileStateReader

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)
    (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    (trw_dir / "reflections").mkdir(exist_ok=True)
    (trw_dir / "context").mkdir(exist_ok=True)
    (trw_dir / "patterns").mkdir(exist_ok=True)

    config = TRWConfig(trw_dir=str(trw_dir))
    reader = FileStateReader()
    llm = MagicMock()
    llm.available = False

    return {
        "scope": "root",
        "target_dir": None,
        "config": config,
        "reader": reader,
        "llm": llm,
    }


def _run_sync(tmp_path: Path, **kwargs: object) -> dict[str, object]:
    """Run execute_claude_md_sync with mocked infrastructure."""
    from trw_mcp.state.claude_md._sync import execute_claude_md_sync

    args = _make_sync_args(tmp_path)
    args.update(kwargs)

    with (
        patch("trw_mcp.state.claude_md._sync.collect_promotable_learnings", return_value=[]),
        patch("trw_mcp.state.claude_md._sync.collect_patterns", return_value=[]),
        patch("trw_mcp.state.claude_md._sync.collect_context_data", return_value=({}, {})),
        patch("trw_mcp.state.claude_md.resolve_trw_dir", return_value=tmp_path / ".trw"),
        patch("trw_mcp.state.claude_md.resolve_project_root", return_value=tmp_path),
        patch("trw_mcp.state.analytics.update_analytics_sync"),
        patch("trw_mcp.state.analytics.mark_promoted"),
    ):
        return execute_claude_md_sync(**args)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# TestInstructionsSync: FR13
# ---------------------------------------------------------------------------


class TestInstructionsSync:
    """FR13: Instructions sync writes to AGENTS.md for opencode clients."""

    def test_fr13_backward_compat_no_client_writes_claude_md(self, tmp_path: Path) -> None:
        """Calling without client parameter still writes CLAUDE.md (backward compat)."""
        (tmp_path / "CLAUDE.md").write_text("# My Project\n", encoding="utf-8")

        result = _run_sync(tmp_path)

        assert result["status"] == "synced"
        claude_md = tmp_path / "CLAUDE.md"
        assert claude_md.exists()
        content = claude_md.read_text(encoding="utf-8")
        assert TRW_MARKER_START in content
        assert TRW_MARKER_END in content

    def test_fr13_no_opencode_dir_does_not_write_agents_md(self, tmp_path: Path) -> None:
        """With no opencode config present, AGENTS.md is not created by auto-detection."""
        result = _run_sync(tmp_path)

        agents_md = tmp_path / "AGENTS.md"
        # agents_md_synced should be False when opencode not detected
        assert result["agents_md_synced"] is False
        # AGENTS.md should not exist (was not created)
        assert not agents_md.exists()

    def test_fr13_writes_agents_md_when_opencode_dir_present(self, tmp_path: Path) -> None:
        """With .opencode/ directory, AGENTS.md is written on auto-detection."""
        (tmp_path / ".opencode").mkdir()

        result = _run_sync(tmp_path, client="auto")

        agents_md = tmp_path / "AGENTS.md"
        assert agents_md.exists(), "AGENTS.md should be created when .opencode/ is detected"
        content = agents_md.read_text(encoding="utf-8")
        assert TRW_MARKER_START in content
        assert TRW_MARKER_END in content
        assert result["agents_md_synced"] is True

    def test_fr13_writes_agents_md_when_opencode_json_present(self, tmp_path: Path) -> None:
        """With opencode.json file, AGENTS.md is written on auto-detection."""
        (tmp_path / "opencode.json").write_text('{"mcp": {}}', encoding="utf-8")

        result = _run_sync(tmp_path, client="auto")

        agents_md = tmp_path / "AGENTS.md"
        assert agents_md.exists(), "AGENTS.md should be created when opencode.json is detected"
        assert result["agents_md_synced"] is True

    def test_fr13_writes_agents_md_when_codex_dir_present(self, tmp_path: Path) -> None:
        """With .codex/ directory, AGENTS.md is written on auto-detection."""
        (tmp_path / ".codex").mkdir()

        result = _run_sync(tmp_path, client="auto")

        agents_md = tmp_path / "AGENTS.md"
        assert agents_md.exists(), "AGENTS.md should be created when .codex/ is detected"
        content = agents_md.read_text(encoding="utf-8")
        assert "OpenAI developer docs MCP server" in content
        assert result["agents_md_synced"] is True

    def test_fr13_writes_both_when_both_detected(self, tmp_path: Path) -> None:
        """With both .claude/ and .opencode/, both CLAUDE.md and AGENTS.md are written."""
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".opencode").mkdir()
        (tmp_path / "CLAUDE.md").write_text("# Project\n", encoding="utf-8")

        result = _run_sync(tmp_path, client="auto")

        claude_md = tmp_path / "CLAUDE.md"
        agents_md = tmp_path / "AGENTS.md"
        assert claude_md.exists()
        assert agents_md.exists()
        assert TRW_MARKER_START in claude_md.read_text(encoding="utf-8")
        assert TRW_MARKER_START in agents_md.read_text(encoding="utf-8")
        assert result["agents_md_synced"] is True

    def test_fr13_client_override_opencode_only(self, tmp_path: Path) -> None:
        """client='opencode' writes AGENTS.md only, not CLAUDE.md."""
        (tmp_path / "CLAUDE.md").write_text("# Existing\n", encoding="utf-8")

        result = _run_sync(tmp_path, client="opencode")

        agents_md = tmp_path / "AGENTS.md"
        assert agents_md.exists(), "AGENTS.md must be written with client='opencode'"
        assert result["agents_md_synced"] is True

        # CLAUDE.md should not have TRW markers injected
        claude_md = tmp_path / "CLAUDE.md"
        claude_content = claude_md.read_text(encoding="utf-8")
        assert TRW_MARKER_START not in claude_content, "CLAUDE.md should NOT be modified when client='opencode'"

    def test_fr13_client_override_codex_only(self, tmp_path: Path) -> None:
        """client='codex' writes Codex-specific AGENTS.md only, not CLAUDE.md."""
        (tmp_path / "CLAUDE.md").write_text("# Existing\n", encoding="utf-8")

        result = _run_sync(tmp_path, client="codex")

        agents_md = tmp_path / "AGENTS.md"
        assert agents_md.exists(), "AGENTS.md must be written with client='codex'"
        content = agents_md.read_text(encoding="utf-8")
        assert "OpenAI developer docs MCP server" in content
        assert "Agent Teams" not in content
        assert result["agents_md_synced"] is True

        claude_content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert TRW_MARKER_START not in claude_content

    def test_fr13_codex_profile_keeps_codex_specific_agents_md(self, tmp_path: Path) -> None:
        """A real Codex profile should still render the Codex-specific AGENTS.md template."""
        from trw_mcp.models.config import TRWConfig

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
        (trw_dir / "reflections").mkdir(exist_ok=True)
        (trw_dir / "context").mkdir(exist_ok=True)
        (trw_dir / "patterns").mkdir(exist_ok=True)

        config = TRWConfig(trw_dir=str(trw_dir))
        object.__setattr__(config, "target_platforms", ["codex"])

        result = _run_sync(tmp_path, client="codex", config=config)

        agents_md = tmp_path / "AGENTS.md"
        assert agents_md.exists(), "AGENTS.md must be written for Codex projects"
        content = agents_md.read_text(encoding="utf-8")
        assert "## Codex Workflow" in content
        assert "OpenAI developer docs MCP server" in content
        assert result["agents_md_synced"] is True

    def test_fr13_client_override_claude_code_only(self, tmp_path: Path) -> None:
        """client='claude-code' writes only CLAUDE.md, not AGENTS.md."""
        (tmp_path / ".opencode").mkdir()  # presence should not trigger AGENTS.md
        (tmp_path / "CLAUDE.md").write_text("# My Project\n", encoding="utf-8")

        result = _run_sync(tmp_path, client="claude-code")

        agents_md = tmp_path / "AGENTS.md"
        assert not agents_md.exists(), "AGENTS.md must NOT be created with client='claude-code'"
        assert result["agents_md_synced"] is False

        claude_md = tmp_path / "CLAUDE.md"
        assert TRW_MARKER_START in claude_md.read_text(encoding="utf-8")

    def test_fr13_client_all_writes_both(self, tmp_path: Path) -> None:
        """client='all' writes both CLAUDE.md and AGENTS.md regardless of detection."""
        (tmp_path / "CLAUDE.md").write_text("# Project\n", encoding="utf-8")

        result = _run_sync(tmp_path, client="all")

        assert (tmp_path / "CLAUDE.md").exists()
        assert (tmp_path / "AGENTS.md").exists()
        assert result["agents_md_synced"] is True

    def test_fr13_same_markers_in_agents_md(self, tmp_path: Path) -> None:
        """AGENTS.md uses <!-- trw:start --> / <!-- trw:end --> markers."""
        (tmp_path / ".opencode").mkdir()

        _run_sync(tmp_path, client="auto")

        agents_md = tmp_path / "AGENTS.md"
        content = agents_md.read_text(encoding="utf-8")
        assert TRW_MARKER_START in content, f"Missing {TRW_MARKER_START!r} in AGENTS.md"
        assert TRW_MARKER_END in content, f"Missing {TRW_MARKER_END!r} in AGENTS.md"

    def test_fr13_agents_md_has_platform_generic_content(self, tmp_path: Path) -> None:
        """AGENTS.md gets platform-generic content, distinct from CLAUDE.md."""
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".opencode").mkdir()
        (tmp_path / "CLAUDE.md").write_text("# My Project\n", encoding="utf-8")

        _run_sync(tmp_path, client="all")

        claude_content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        agents_content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

        # Both must have TRW markers
        assert TRW_MARKER_START in claude_content
        assert TRW_MARKER_START in agents_content

        # AGENTS.md should have platform-generic content (no Claude-specific terms)
        assert "Agent Teams" not in agents_content
        assert "subagents" not in agents_content
        assert "/trw-ceremony-guide" not in agents_content
        assert "MCP (Model Context Protocol)" in agents_content

        # CLAUDE.md should have orchestration-specific content
        assert "orchestration" in claude_content

    def test_fr13_auto_no_ide_defaults_to_claude(self, tmp_path: Path) -> None:
        """With client='auto' and no IDE dirs, defaults to writing CLAUDE.md only."""
        (tmp_path / "CLAUDE.md").write_text("# My Project\n", encoding="utf-8")

        result = _run_sync(tmp_path, client="auto")

        claude_md = tmp_path / "CLAUDE.md"
        assert claude_md.exists()
        assert TRW_MARKER_START in claude_md.read_text(encoding="utf-8")
        # No opencode detected, so AGENTS.md should not exist
        assert not (tmp_path / "AGENTS.md").exists()
        assert result["agents_md_synced"] is False

    def test_fr13_result_includes_agents_md_path(self, tmp_path: Path) -> None:
        """Result includes agents_md_path when AGENTS.md is written."""
        (tmp_path / ".opencode").mkdir()

        result = _run_sync(tmp_path, client="auto")

        assert result["agents_md_path"] is not None
        assert "AGENTS.md" in str(result["agents_md_path"])

    def test_fr13_result_agents_md_path_none_when_not_written(self, tmp_path: Path) -> None:
        """Result has agents_md_path=None when AGENTS.md is not written."""
        result = _run_sync(tmp_path, client="claude-code")

        assert result["agents_md_path"] is None

    def test_fr13_agents_md_preserves_user_content(self, tmp_path: Path) -> None:
        """Existing AGENTS.md user content outside TRW markers is preserved."""
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text(
            f"# AGENTS.md\n\nUser content here.\n\n{TRW_MARKER_START}\nOld TRW section\n{TRW_MARKER_END}\n",
            encoding="utf-8",
        )
        (tmp_path / ".opencode").mkdir()

        _run_sync(tmp_path, client="auto")

        content = agents_md.read_text(encoding="utf-8")
        assert "User content here." in content, "User content should be preserved in AGENTS.md"
        assert "Old TRW section" not in content, "Old TRW section should be replaced"
        assert TRW_MARKER_START in content

    def test_fr13_tool_accepts_client_parameter(self, tmp_path: Path) -> None:
        """The MCP tool trw_claude_md_sync accepts a client parameter."""
        from tests.conftest import get_tools_sync, make_test_server

        server = make_test_server("learning")
        tools = get_tools_sync(server)

        assert "trw_claude_md_sync" in tools, "trw_claude_md_sync must still be registered"

        tool = tools["trw_claude_md_sync"]
        # The tool schema should expose the client parameter
        import inspect

        sig = inspect.signature(tool.fn)
        assert "client" in sig.parameters, "trw_claude_md_sync must accept a 'client' parameter"
