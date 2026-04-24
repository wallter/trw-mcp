"""Tests for CLAUDE.md / AGENTS.md sync: FR13 instructions sync generalization.

Covers:
  - FR13: client parameter routes writes to CLAUDE.md and/or AGENTS.md
  - FR13: auto-detection via detect_ide() drives default behavior
  - FR13: backward compatibility — trw_claude_md_sync still writes CLAUDE.md
  - FR13: AGENTS.md uses same markers and identical TRW section content
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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


# ---------------------------------------------------------------------------
# PRD-QUAL-075 FR11: marker preservation and sync target scope.
# ---------------------------------------------------------------------------


class TestMarkerPreservation:
    """FR11: trw:start / trw:end markers must survive a sync cycle."""

    def test_markers_preserved_after_sync(self, tmp_path: Path) -> None:
        """Running sync twice is idempotent — markers remain exactly once."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Project\n\nUser prose.\n", encoding="utf-8")

        _run_sync(tmp_path)
        first = claude_md.read_text(encoding="utf-8")
        assert first.count(TRW_MARKER_START) == 1
        assert first.count(TRW_MARKER_END) == 1

        _run_sync(tmp_path)
        second = claude_md.read_text(encoding="utf-8")
        assert second.count(TRW_MARKER_START) == 1, "sync duplicated the start marker"
        assert second.count(TRW_MARKER_END) == 1, "sync duplicated the end marker"
        # User content preserved.
        assert "User prose." in second

    def test_sync_does_not_recreate_markers_in_trw_mcp_claude_md(self, tmp_path: Path) -> None:
        """FR05/FR11: sync operates on the project root CLAUDE.md only — it must
        not rewrite the package-local ``trw-mcp/CLAUDE.md`` which now lives
        without trw markers and instead points at the canonical docs.
        """
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Project\n", encoding="utf-8")

        # Simulate the package-local file under a nested path — sync should
        # not touch it because its target is the root-level CLAUDE.md.
        nested = tmp_path / "trw-mcp"
        nested.mkdir()
        nested_claude = nested / "CLAUDE.md"
        nested_claude.write_text("# trw-mcp\n\nNo markers here.\n", encoding="utf-8")

        _run_sync(tmp_path)

        # Root got markers.
        assert TRW_MARKER_START in claude_md.read_text(encoding="utf-8")
        # Nested did not.
        nested_content = nested_claude.read_text(encoding="utf-8")
        assert TRW_MARKER_START not in nested_content
        assert TRW_MARKER_END not in nested_content


# ---------------------------------------------------------------------------
# PRD-QUAL-075 FR06: second-profile parity check (opencode).
# ---------------------------------------------------------------------------


_FIXTURE_DIR = Path(__file__).parent / "fixtures"
_OPENCODE_SHA_PATH = _FIXTURE_DIR / "opencode_agents_md_baseline.sha256"


class TestOpencodeParity:
    """FR06 acceptance: sanity-check a second profile's rendered artifact is stable."""

    def test_opencode_parity(self, tmp_path: Path) -> None:
        """Render AGENTS.md via opencode profile; assert SHA256 matches baseline.

        If the baseline fixture is absent, capture it (``--fixture-generated``
        semantics) so a subsequent run enforces stability.
        """
        (tmp_path / ".opencode").mkdir()

        _run_sync(tmp_path, client="opencode")

        agents_md = tmp_path / "AGENTS.md"
        assert agents_md.exists(), "opencode sync must produce AGENTS.md"

        content = agents_md.read_bytes()
        actual_sha = hashlib.sha256(content).hexdigest()

        if not _OPENCODE_SHA_PATH.exists():
            # First-run capture: write baseline so the next run enforces parity.
            _OPENCODE_SHA_PATH.write_text(actual_sha + "\n", encoding="utf-8")
            pytest.skip(
                f"Captured opencode AGENTS.md baseline SHA at {_OPENCODE_SHA_PATH.name} "
                "(--fixture-generated). Re-run to enforce."
            )

        expected_sha = _OPENCODE_SHA_PATH.read_text(encoding="utf-8").strip()
        assert actual_sha == expected_sha, (
            f"opencode AGENTS.md SHA256 drifted: expected {expected_sha}, got {actual_sha}. "
            "If change is intentional, regenerate opencode_agents_md_baseline.sha256."
        )

    def test_opencode_agents_md_has_no_claude_code_literal(self, tmp_path: Path) -> None:
        """PRD-CORE-149 FR08 acceptance: the written AGENTS.md must not carry
        any literal 'Claude Code' string.

        This is the sync-pipeline end-to-end check the parity SHA alone cannot
        provide — a baseline captured with the literal present would freeze
        the bug into the fixture. Grepping the written file directly closes
        the profile-awareness regression surface at the actual sync output,
        not just the renderer.
        """
        (tmp_path / ".opencode").mkdir()
        _run_sync(tmp_path, client="opencode")

        agents_md = tmp_path / "AGENTS.md"
        assert agents_md.exists(), "opencode sync must produce AGENTS.md"

        content = agents_md.read_text(encoding="utf-8")
        assert "Claude Code" not in content, (
            "opencode AGENTS.md contains a literal 'Claude Code' string — "
            "profile-awareness regression. Every nudge/protocol template "
            "must use {client_display_name} substitution (PRD-CORE-149 FR02)."
        )


# ---------------------------------------------------------------------------
# PRD-QUAL-075 US-002 acceptance: canonical-edit-propagates.
# ---------------------------------------------------------------------------


class TestCanonicalEditPropagates:
    """US-002 acceptance: edits to canonical docs propagate via trw_instructions_sync.

    Per exec plan W2: the renderer does NOT currently read canonical files; it
    renders from static strings. This test documents the expected future
    behavior and is marked xfail until a follow-up PRD (PRD-QUAL-076) wires the
    renderer to the canonical docs.
    """

    @pytest.mark.xfail(
        reason=(
            "FR03/FR04 extraction is doc-only this sprint; sync still renders "
            "from static strings. Propagation to be wired in follow-up PRD-QUAL-076."
        ),
        strict=False,
    )
    def test_canonical_edit_propagates(self, tmp_path: Path) -> None:
        sentinel = f"<!-- EDIT-TEST-{uuid.uuid4()} -->"

        # Locate the canonical tool-lifecycle doc (read-only snapshot — we are
        # not mutating the real file; we mock renderer resolution through a
        # temporary copy).
        repo_root = Path(__file__).resolve().parents[2]
        canonical = repo_root / "docs" / "documentation" / "tool-lifecycle.md"
        if not canonical.exists():
            pytest.skip("canonical tool-lifecycle.md not found — FR03 not yet landed")

        # Simulate "edit": create a tmp copy with the sentinel injected near the top.
        tmp_canonical = tmp_path / "tool-lifecycle.md"
        original_text = canonical.read_text(encoding="utf-8")
        edited_text = sentinel + "\n" + original_text
        tmp_canonical.write_text(edited_text, encoding="utf-8")

        # Run sync. If renderer reads canonical docs, sentinel will appear in
        # the rendered CLAUDE.md. Under current (static-string) renderer, it
        # will not — so this xfails as expected.
        (tmp_path / "CLAUDE.md").write_text("# Project\n", encoding="utf-8")
        _run_sync(tmp_path)

        rendered = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert sentinel in rendered, (
            "Canonical edit did not propagate to rendered CLAUDE.md — "
            "renderer still reads from static strings (expected until PRD-QUAL-076)."
        )
