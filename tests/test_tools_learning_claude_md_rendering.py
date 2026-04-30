"""Tests for CLAUDE.md template loading and rendering helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_memory.models.memory import MemoryEntry
from trw_mcp.state.claude_md import (
    CEREMONY_TOOLS,
    load_claude_md_template,
    render_ceremony_flows,
    render_ceremony_quick_ref,
    render_ceremony_table,
    render_closing_reminder,
    render_delegation_protocol,
    render_imperative_opener,
    render_memory_harmonization,
    render_phase_descriptions,
    render_shared_learnings,
    render_template,
)

from tests._tools_learning_shared import _CFG, _get_tools, _write_analytics, set_project_root

class TestClaudeMdTemplate:
    """Tests for the CLAUDE.md template system (PRD-CORE-002 Phase 1)."""

    def test_loads_bundled_template(self, tmp_path: Path) -> None:
        """Default template loaded from package data (CORE-093 compact format)."""
        template = load_claude_md_template(tmp_path / _CFG.trw_dir)
        # CORE-093 FR07: template reduced to 4 compact variables
        assert "{{imperative_opener}}" in template
        assert "{{ceremony_quick_ref}}" in template
        assert "trw:start" in template
        assert "trw:end" in template

    def test_project_override_takes_precedence(self, tmp_path: Path) -> None:
        """Project-local template in .trw/templates/ overrides bundled."""
        trw_dir = tmp_path / _CFG.trw_dir
        templates_dir = trw_dir / _CFG.templates_dir
        templates_dir.mkdir(parents=True)
        custom = "<!-- trw:start -->\n## Custom Section\n{{categorized_learnings}}<!-- trw:end -->\n"
        (templates_dir / "claude_md.md").write_text(custom, encoding="utf-8")

        template = load_claude_md_template(trw_dir)
        assert "Custom Section" in template
        assert "{{categorized_learnings}}" in template

    def test_render_replaces_placeholders(self, tmp_path: Path) -> None:
        """{{key}} tokens replaced with content."""
        template = "## Title\n\n{{section_a}}{{section_b}}"
        context = {"section_a": "### A\n- item\n\n", "section_b": "### B\n- item\n\n"}
        result = render_template(template, context)
        assert "### A" in result
        assert "### B" in result
        assert "{{section_a}}" not in result

    def test_render_empty_sections_collapse(self, tmp_path: Path) -> None:
        """Empty values don't leave runs of blank lines."""
        template = "Header\n\n{{a}}{{b}}Footer"
        context = {"a": "", "b": ""}
        result = render_template(template, context)
        # Should not have 3+ consecutive newlines
        assert "\n\n\n" not in result

    def test_sync_uses_template_produces_same_output(self, tmp_path: Path) -> None:
        """trw_claude_md_sync with bundled template produces equivalent output."""
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Template sync test learning",
            detail="Verify template produces same output",
            tags=["gotcha"],
            impact=0.9,
        )

        result = tools["trw_claude_md_sync"].fn(scope="root")
        assert result["status"] == "synced"
        # CORE-093: learning promotion removed
        assert result["learnings_promoted"] == 0

        claude_md = tmp_path / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")
        assert "trw:start" in content
        assert "trw:end" in content

    def test_custom_template_with_extra_sections(self, tmp_path: Path) -> None:
        """Custom templates can add static content alongside placeholders."""
        trw_dir = tmp_path / _CFG.trw_dir
        templates_dir = trw_dir / _CFG.templates_dir
        templates_dir.mkdir(parents=True)

        custom_template = (
            "\n"
            "<!-- TRW AUTO-GENERATED \u2014 do not edit between markers -->\n"
            "<!-- trw:start -->\n"
            "\n"
            "### Project-Specific Notes\n"
            "- This project uses React 19\n"
            "\n"
            "{{imperative_opener}}"
            "{{ceremony_quick_ref}}"
            "{{memory_harmonization}}"
            "{{shared_learnings}}"
            "{{closing_reminder}}"
            "<!-- trw:end -->\n"
        )
        (templates_dir / "claude_md.md").write_text(custom_template, encoding="utf-8")

        tools = _get_tools()

        result = tools["trw_claude_md_sync"].fn(scope="root")
        assert result["status"] == "synced"

        claude_md = tmp_path / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")
        assert "Project-Specific Notes" in content
        assert "React 19" in content

class TestCeremonyRendering:
    """Tests for ceremony tool guidance rendering (auto-generated in CLAUDE.md)."""

    def test_render_phase_descriptions(self) -> None:
        """All 6 phases present with arrow diagram."""
        result = render_phase_descriptions()
        assert "### Execution Phases" in result
        assert "RESEARCH" in result
        assert "IMPLEMENT" in result
        assert "DELIVER" in result
        # All 6 descriptions
        for name in ("RESEARCH", "PLAN", "IMPLEMENT", "VALIDATE", "REVIEW", "DELIVER"):
            assert f"**{name}**" in result

    def test_render_ceremony_table(self) -> None:
        """Table headers and all 11 tools listed."""
        result = render_ceremony_table()
        assert "### Tool Lifecycle" in result
        assert "| Phase | Tool | When to Use | What It Does | Example |" in result
        assert "|-------|------|-------------|--------------|---------|" in result
        # All 11 tools present
        for ct in CEREMONY_TOOLS:
            assert f"`{ct.tool}`" in result
        assert len(CEREMONY_TOOLS) == 12

    def test_render_ceremony_flows(self) -> None:
        """Both quick and full flows present with key tool names."""
        result = render_ceremony_flows()
        assert "**Quick Task**" in result
        assert "**Full Run**" in result
        assert "trw_session_start" in result
        assert "trw_deliver()" in result

    def test_render_imperative_opener(self) -> None:
        """Imperative opener defines orchestrator role and names ceremony tools."""
        result = render_imperative_opener()
        # Orchestrator role identity
        assert "orchestration" in result.lower()
        assert "delegate" in result.lower()
        # Ceremony tools mentioned (brief — ceremony_quick_ref has the full table)
        assert "trw_session_start()" in result
        assert "trw_checkpoint()" in result
        assert "trw_deliver()" in result

    def test_render_imperative_opener_uses_analytics_counts(self, tmp_path: Path) -> None:
        """FR06: opener claims use analytics-driven learning/session counts."""
        _write_analytics(tmp_path, sessions_tracked=0, total_learnings=0)

        result = render_imperative_opener()

        assert "0 learnings from 0 prior sessions" in result

    def test_render_closing_reminder(self) -> None:
        """Closing reminder bookends with session boundaries."""
        result = render_closing_reminder()
        assert "### Session Boundaries" in result
        assert "trw_session_start()" in result
        # PRD-CORE-062-FR01: trw_deliver removed from closing reminder (redundant with opener)
        assert "trw_deliver()" not in result
        assert "compounds" in result

    def test_render_memory_harmonization(self) -> None:
        """Memory routing section disambiguates trw_learn vs native auto-memory."""
        result = render_memory_harmonization()
        # Heading
        assert "### Memory Routing" in result
        # Default action (trw_learn as default, not native)
        assert "trw_learn()" in result
        assert "native auto-memory" in result.lower()
        # Comparison table columns
        assert "trw_recall(query)" in result
        assert "Visibility" in result
        assert "Lifecycle" in result
        # Concrete routing examples
        assert "native memory" in result.lower()
        # Claude Code-specific — should NOT mention opencode
        assert "opencode" not in result.lower()
        assert "AGENTS.md" not in result

    def test_render_shared_learnings(self) -> None:
        """Shared learnings section renders top org memories compactly."""
        entries = [
            MemoryEntry(
                id="M-1",
                content="Cross-project deployment lesson",
                detail="Use staged rollouts before schema flips.",
                namespace="project:other",
                importance=0.9,
                cross_validated=True,
            )
        ]

        with patch("trw_mcp.state.claude_md._static_sections.list_org_shared_entries", return_value=entries):
            result = render_shared_learnings()

        assert "## Shared Learnings" in result
        assert "Cross-project deployment lesson" in result
        assert "Use staged rollouts before schema flips." in result

    def test_render_delegation_protocol(self) -> None:
        """Delegation protocol contains orchestrator role, decision tree, and value framing."""
        result = render_delegation_protocol()
        assert "## TRW Delegation & Orchestration" in result
        assert "### When to Delegate" in result
        # Orchestrator role responsibilities
        assert "orchestrator" in result.lower()
        assert "delegate" in result.lower()
        assert "verify" in result.lower()
        # Decision tree keywords
        assert "Trivial?" in result
        assert "Subagent" in result
        assert "Agent Team" in result
        assert "Self-implement" in result
        # Role-focused framing (not CRITICAL/ALWAYS/NEVER)
        assert "teammates do the implementation" in result

    def test_bundled_template_has_ceremony_placeholders(self) -> None:
        """Bundled template contains CORE-093 compact placeholder tokens."""
        # Use a non-existent trw_dir so it falls through to bundled template
        template = load_claude_md_template(Path("/nonexistent/.trw"))
        # CORE-093 FR07: template reduced to 4 compact variables
        assert "{{imperative_opener}}" in template
        assert "{{ceremony_quick_ref}}" in template
        assert "{{memory_harmonization}}" in template
        assert "{{shared_learnings}}" in template
        assert "{{closing_reminder}}" in template

    def test_sync_includes_ceremony_sections(self, tmp_path: Path) -> None:
        """CLAUDE.md has compact protocol; ceremony details in hook only."""
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Ceremony sync test",
            detail="Verify ceremony sections in output",
            impact=0.9,
        )

        result = tools["trw_claude_md_sync"].fn(scope="root")
        assert result["status"] == "synced"

        claude_md = tmp_path / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")
        # Ceremony details moved to session-start hook — NOT in CLAUDE.md
        assert "### Execution Phases" not in content
        assert "### Tool Lifecycle" not in content
        assert "## TRW Delegation & Orchestration" not in content
        assert "## Rationalization Watchlist" not in content
        # Quick ref card present with skill pointer
        assert "/trw-ceremony-guide" in content
        # Strong session_start trigger in opener
        assert "orchestration" in content.lower()
        assert "trw_session_start()" in content
        assert "first action" in content.lower()
        # Memory routing section present
        assert "Memory Routing" in content
        # Closing reminder bookends the section
        assert "Session Boundaries" in content
        # No unreplaced placeholders
        assert "{{imperative_opener}}" not in content
        assert "{{ceremony_phases}}" not in content
        assert "{{ceremony_table}}" not in content
        assert "{{ceremony_flows}}" not in content
        assert "{{closing_reminder}}" not in content
        assert "{{ceremony_quick_ref}}" not in content
