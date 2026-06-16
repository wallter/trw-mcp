"""Tests for progressive disclosure and behavioral protocol rendering."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._tools_learning_shared import _CFG, _get_tools, _write_analytics
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.claude_md import (
    render_adherence,
    render_agents_trw_section,
    render_behavioral_protocol,
    render_ceremony_quick_ref,
    render_closing_reminder,
    render_memory_harmonization,
)
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

# Project-root / trw-dir isolation is provided by the autouse conftest
# ``_isolate_trw_dir`` fixture, which patches the source module
# ``trw_mcp.state._paths``. The claude_md dispatcher and section renderers
# (render_behavioral_protocol, render_memory_harmonization,
# render_agents_trw_section) late-resolve through that module at call time, so
# no claude_md-specific binding patch is required here.


class TestProgressiveDisclosure:
    """Tests for PRD-CORE-061 progressive disclosure and PRD-CORE-062 context optimization."""

    def test_auto_gen_line_count_within_limit(self, tmp_path: Path) -> None:
        """Rendered auto-gen block <=300 lines (expanded from 80 for full ceremony rendering)."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Line count test",
            detail="Testing line count",
            impact=0.9,
        )
        tools["trw_claude_md_sync"].fn(scope="root")
        claude_md = tmp_path / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")
        # Count lines between markers
        start = content.index("<!-- trw:start -->")
        end = content.index("<!-- trw:end -->") + len("<!-- trw:end -->")
        auto_gen = content[start:end]
        line_count = auto_gen.count("\n")
        assert line_count <= 300, f"Auto-gen section is {line_count} lines, exceeds 300"

    def test_auto_gen_includes_learnings(self, tmp_path: Path) -> None:
        """CORE-093: CLAUDE.md no longer includes individual learnings."""
        tools = _get_tools()
        for i in range(5):
            tools["trw_learn"].fn(
                summary=f"High impact learning {i}",
                detail=f"Detail for learning {i}",
                impact=0.9,
            )
        result = tools["trw_claude_md_sync"].fn(scope="root")
        # CORE-093: learnings_promoted always 0
        assert result["learnings_promoted"] == 0
        claude_md = tmp_path / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")
        assert "trw:start" in content

    def test_auto_gen_contains_skill_reference(self, tmp_path: Path) -> None:
        """PRD-CORE-061-FR02: rendered output contains /trw-ceremony-guide."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Skill ref test",
            detail="Testing",
            impact=0.9,
        )
        tools["trw_claude_md_sync"].fn(scope="root")
        claude_md = tmp_path / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")
        assert "/trw-ceremony-guide" in content

    def test_auto_gen_no_tool_lifecycle_table(self, tmp_path: Path) -> None:
        """Tool lifecycle table is in session-start hook, not CLAUDE.md."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Table test",
            detail="Testing",
            impact=0.9,
        )
        tools["trw_claude_md_sync"].fn(scope="root")
        claude_md = tmp_path / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")
        assert "| Phase | Tool |" not in content

    def test_auto_gen_no_rationalization_watchlist(self, tmp_path: Path) -> None:
        """Rationalization watchlist is in session-start hook, not CLAUDE.md."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Watchlist test",
            detail="Testing",
            impact=0.9,
        )
        tools["trw_claude_md_sync"].fn(scope="root")
        claude_md = tmp_path / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")
        assert "Rationalization Watchlist" not in content

    def test_auto_gen_no_orphan_headers(self, tmp_path: Path) -> None:
        """PRD-CORE-061-FR01: no orphan ## TRW ... (Auto-Generated) headers."""
        import re

        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Header test",
            detail="Testing",
            impact=0.9,
        )
        tools["trw_claude_md_sync"].fn(scope="root")
        claude_md = tmp_path / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")
        start = content.index("<!-- trw:start -->")
        end = content.index("<!-- trw:end -->")
        auto_gen = content[start:end]
        # Check for orphan headers: ## TRW Ceremony Tools (Auto-Generated) etc
        orphan_pattern = r"## TRW (Ceremony Tools|Learnings|Delegation) \(Auto-Generated\)"
        matches = re.findall(orphan_pattern, auto_gen)
        assert len(matches) == 0, f"Found orphan headers: {matches}"

    def test_max_auto_lines_gate_raises_error(self, tmp_path: Path, reader: FileStateReader) -> None:
        """PRD-CORE-061-FR04: StateError raised when auto-gen exceeds limit."""
        from trw_mcp.clients.llm import LLMClient
        from trw_mcp.exceptions import StateError
        from trw_mcp.state.claude_md import execute_claude_md_sync

        config = TRWConfig(max_auto_lines=5)  # Very low limit
        llm = LLMClient()
        with pytest.raises(StateError, match="exceeds max_auto_lines=5"):
            execute_claude_md_sync("root", None, config, reader, llm)

    def test_max_auto_lines_gate_passes_at_limit(self, tmp_path: Path) -> None:
        """PRD-CORE-061-FR04: exactly max_auto_lines succeeds."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Gate pass test",
            detail="Testing",
            impact=0.9,
        )
        # Default max_auto_lines=80, our output should be well under
        result = tools["trw_claude_md_sync"].fn(scope="root")
        assert result["status"] == "synced"

    def test_max_auto_lines_config_default(self) -> None:
        """TRWConfig().max_auto_lines == 300 (expanded for full ceremony rendering)."""
        config = TRWConfig()
        assert config.max_auto_lines == 300

    def test_max_auto_lines_config_override(self) -> None:
        """PRD-CORE-061-FR04: TRWConfig(max_auto_lines=100) works."""
        config = TRWConfig(max_auto_lines=100)
        assert config.max_auto_lines == 100

    def test_render_ceremony_quick_ref(self) -> None:
        """PRD-CORE-061-FR02: quick ref card contains 4 ceremony-critical tools."""
        result = render_ceremony_quick_ref()
        assert "## TRW Behavioral Protocol (Auto-Generated)" in result
        assert "trw_session_start()" in result
        assert "trw_checkpoint(message)" in result
        assert "trw_learn(summary, detail)" in result
        assert "trw_deliver()" in result
        assert "/trw-ceremony-guide" in result

    def test_render_memory_harmonization_uses_analytics_counts(self, tmp_path: Path) -> None:
        """FR06: memory routing scale claim reflects tracked analytics."""
        _write_analytics(tmp_path, sessions_tracked=12, total_learnings=34)
        # The conftest _isolate_trw_dir fixture points resolve_project_root at
        # tmp_path (late-resolved by the renderer); clear the turn-scoped
        # analytics cache so the freshly-written tmp analytics.yaml is re-read.
        from trw_mcp.state.claude_md.sections._memory_routing import _analytics_cache

        _analytics_cache.set(None)

        result = render_memory_harmonization()

        assert "34 learnings across 12 sessions" in result

    def test_render_agents_trw_section_uses_analytics_counts(self, tmp_path: Path) -> None:
        """FR06: AGENTS-facing TRW section uses analytics-backed counts."""
        _write_analytics(tmp_path, sessions_tracked=7, total_learnings=19)
        from trw_mcp.state.claude_md.sections._memory_routing import _analytics_cache

        _analytics_cache.set(None)

        result = render_agents_trw_section()

        assert "loads 19 learnings from 7 prior sessions and recovers any active run" in result
        assert "load context from 7 prior sessions" in result

    def test_closing_reminder_includes_deliver_gate(self) -> None:
        """PRD-CORE-062-FR01 / v26: render_closing_reminder carries deliver-gate language.

        The deliver gate (Do NOT call trw_deliver unless build_check pass / acceptable-failure
        / explicit override) was added to render_closing_reminder in 86da33ef. The prior
        assertion that trw_deliver was absent is now invalid; verify the gate is present.
        """
        result = render_closing_reminder()
        assert "trw_deliver" in result
        assert "Deliver Gate" in result
        assert "trw_build_check" in result
        assert "compounds across sessions" in result

    def test_render_agents_trw_section_includes_deliver_gate(self, tmp_path: Path) -> None:
        """AGENTS.md TRW section must carry deliver-gate language for light clients.

        Light clients (opencode, cursor-cli, aider, copilot) receive AGENTS.md as their
        sole protocol carrier; without the gate statement they silently bypass the
        build_check prerequisite that blocks coding-task delivers.
        """
        from trw_mcp.state.claude_md.sections._memory_routing import _analytics_cache

        _analytics_cache.set(None)
        result = render_agents_trw_section()

        assert "Deliver gate" in result
        assert "trw_build_check" in result
        assert "acceptable-failure" in result

    def test_render_codex_trw_section_includes_deliver_gate(self) -> None:
        """Codex AGENTS.md TRW section must carry deliver-gate language.

        Codex uses render_codex_trw_section for its AGENTS.md; without the gate
        statement Codex agents can call trw_deliver without a passing build_check.
        """
        from trw_mcp.state.claude_md._static_sections import render_codex_trw_section

        result = render_codex_trw_section()

        assert "Deliver gate" in result
        assert "trw_build_check" in result
        assert "acceptable-failure" in result

    def test_session_start_hook_contains_behavioral_protocol_header(self) -> None:
        """PRD-CORE-061-FR03: session-start.sh has formal protocol header."""
        hook_path = Path(__file__).parent.parent / "src" / "trw_mcp" / "data" / "hooks" / "session-start.sh"
        content = hook_path.read_text(encoding="utf-8")
        assert "## TRW Behavioral Protocol" in content

    def test_session_start_rigid_line_count(self) -> None:
        """PRD-CORE-062-FR04: session-start.sh RIGID line count is 1."""
        hook_path = Path(__file__).parent.parent / "src" / "trw_mcp" / "data" / "hooks" / "session-start.sh"
        content = hook_path.read_text(encoding="utf-8")
        rigid_count = content.count("RIGID")
        assert rigid_count == 1, f"RIGID appears {rigid_count} times, expected 1"

    def test_skill_exists_in_data_directory(self) -> None:
        """PRD-CORE-061-FR01: trw-ceremony-guide skill exists."""
        skill_path = (
            Path(__file__).parent.parent / "src" / "trw_mcp" / "data" / "skills" / "trw-ceremony-guide" / "SKILL.md"
        )
        assert skill_path.exists()

    def test_skill_has_minimum_table_rows(self) -> None:
        """PRD-CORE-061-FR01: skill contains >= 12 table rows."""
        skill_path = (
            Path(__file__).parent.parent / "src" / "trw_mcp" / "data" / "skills" / "trw-ceremony-guide" / "SKILL.md"
        )
        content = skill_path.read_text(encoding="utf-8")
        # Count table rows (lines starting with | that aren't headers/separators)
        table_rows = [
            line
            for line in content.split("\n")
            if line.startswith("| ") and "---" not in line and "Phase | Tool" not in line
        ]
        assert len(table_rows) >= 12, f"Only {len(table_rows)} table rows, expected >= 12"

    def test_skill_frontmatter_user_invocable(self) -> None:
        """PRD-CORE-061-FR01: skill has user-invocable: true frontmatter."""
        skill_path = (
            Path(__file__).parent.parent / "src" / "trw_mcp" / "data" / "skills" / "trw-ceremony-guide" / "SKILL.md"
        )
        content = skill_path.read_text(encoding="utf-8")
        assert "user-invocable: true" in content

    def test_behavioral_protocol_yaml_no_watchlist(self) -> None:
        """PRD-CORE-062-FR05: runtime behavioral_protocol.yaml has no unused sections."""
        bp_path = Path(__file__).parent.parent.parent / ".trw" / "context" / "behavioral_protocol.yaml"
        if bp_path.exists():
            content = bp_path.read_text(encoding="utf-8")
            assert "rationalization_watchlist:" not in content
            assert "rigid_tools:" not in content
            assert "flexible_tools:" not in content

    def test_mark_promoted_still_fires(self, tmp_path: Path) -> None:
        """CORE-093: learning promotion removed — sync produces static protocol."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Promotion analytics test",
            detail="Should be promoted for analytics",
            impact=0.9,
        )
        sync_result = tools["trw_claude_md_sync"].fn(scope="root")
        # CORE-093: learnings_promoted always 0
        assert sync_result["learnings_promoted"] == 0
        claude_md = tmp_path / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")
        assert "trw:start" in content


class TestBehavioralProtocol:
    """Tests for behavioral protocol rendering and integration."""

    def test_render_behavioral_protocol_from_yaml(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Renders directives from behavioral_protocol.yaml."""
        # Create protocol file
        context_dir = tmp_path / _CFG.trw_dir / _CFG.context_dir
        writer.ensure_dir(context_dir)
        writer.write_yaml(
            context_dir / "behavioral_protocol.yaml",
            {
                "directives": [
                    "Execute trw_recall at session start",
                    "Read FRAMEWORK.md after compaction",
                ],
            },
        )

        result = render_behavioral_protocol()
        assert "- Execute trw_recall at session start" in result
        assert "- Read FRAMEWORK.md after compaction" in result

    def test_render_behavioral_protocol_empty_when_missing(self, tmp_path: Path) -> None:
        """Returns empty string when protocol file does not exist."""
        result = render_behavioral_protocol()
        assert result == ""

    def test_render_behavioral_protocol_caps_at_12(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Respects _BEHAVIORAL_PROTOCOL_CAP of 12 directives."""
        context_dir = tmp_path / _CFG.trw_dir / _CFG.context_dir
        writer.ensure_dir(context_dir)
        writer.write_yaml(
            context_dir / "behavioral_protocol.yaml",
            {
                "directives": [f"Directive {i}" for i in range(20)],
            },
        )

        result = render_behavioral_protocol()
        # Should have exactly 12 directive lines
        directive_lines = [line for line in result.strip().split("\n") if line.startswith("- ")]
        assert len(directive_lines) == 12

    def test_render_adherence_includes_behavioral_mandate_tag(
        self,
        tmp_path: Path,
    ) -> None:
        """behavioral-mandate tag is recognized by _render_adherence."""
        entries = [
            {
                "summary": "Execute trw_recall at every session start",
                "detail": "Ensures prior learnings are loaded",
                "tags": ["behavioral-mandate", "framework"],
                "impact": 0.9,
            },
        ]
        result = render_adherence(entries)
        assert "Framework Adherence" in result
        assert "Execute trw_recall at every session start" in result

    def test_render_adherence_behavioral_mandate_uses_summary(
        self,
        tmp_path: Path,
    ) -> None:
        """behavioral-mandate entries promote summary, not detail sentences."""
        entries = [
            {
                "summary": "Always execute trw_reflect after implementation",
                "detail": "This is a short detail without must/should keywords.",
                "tags": ["behavioral-mandate"],
                "impact": 0.9,
            },
        ]
        result = render_adherence(entries)
        # Summary should appear (promoted directly)
        assert "Always execute trw_reflect after implementation" in result
        # Detail should NOT appear (no sentence extraction for behavioral-mandate)
        assert "short detail" not in result

    def test_claude_md_sync_includes_behavioral_protocol(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Full trw_claude_md_sync includes compact behavioral protocol (CORE-093)."""
        tools = _get_tools()
        result = tools["trw_claude_md_sync"].fn(scope="root")
        assert result["status"] == "synced"

        claude_md = tmp_path / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")
        # CORE-093: compact protocol with session boundaries reminder
        assert "trw:start" in content
        assert "trw:end" in content
