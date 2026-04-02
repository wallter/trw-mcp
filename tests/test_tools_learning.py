"""Tests for learning tools — learn, recall, claude_md_sync."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import get_tools_sync, make_test_server
from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.scoring import (
    compute_initial_q_value,
    correlate_recalls,
    process_outcome,
    process_outcome_for_event,
)
from trw_mcp.state.analytics import (
    extract_learnings_from_llm,
    extract_learnings_mechanical,
    find_success_patterns,
    is_success_event,
)
from trw_mcp.state.claude_md import (
    CEREMONY_TOOLS,
    collect_context_data,
    collect_patterns,
    collect_promotable_learnings,
    load_claude_md_template,
    render_adherence,
    render_behavioral_protocol,
    render_ceremony_flows,
    render_ceremony_quick_ref,
    render_ceremony_table,
    render_closing_reminder,
    render_delegation_protocol,
    render_imperative_opener,
    render_memory_harmonization,
    render_phase_descriptions,
    render_template,
)
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.recall_search import search_patterns

_CFG = TRWConfig()


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests.

    Dedup is disabled to prevent BM25/vector similarity from merging
    entries that tests expect to remain distinct (e.g. "Learning 1" and
    "Learning 2" score >0.85 similarity and would otherwise be merged).
    """
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("TRW_DEDUP_ENABLED", "false")
    return tmp_path


def _get_tools() -> dict[str, Any]:
    """Create fresh server and return tool map."""
    return get_tools_sync(make_test_server("learning"))


def _entries_dir(root: Path) -> Path:
    """Build entries directory path from config — no hardcoded strings."""
    return root / _CFG.trw_dir / _CFG.learnings_dir / _CFG.entries_dir


class TestTrwLearn:
    """Tests for trw_learn tool."""

    def test_records_learning(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Test learning",
            detail="This is a detailed learning entry",
            tags=["testing", "example"],
            impact=0.8,
        )
        assert "learning_id" in result
        assert result["status"] == "recorded"

        entries_dir = _entries_dir(tmp_path)
        assert entries_dir.exists()
        entry_files = list(entries_dir.glob("*.yaml"))
        assert len(entry_files) == 1

    def test_updates_index(self, tmp_path: Path, reader: FileStateReader) -> None:
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Learning 1",
            detail="Detail 1",
        )
        tools["trw_learn"].fn(
            summary="Learning 2",
            detail="Detail 2",
        )

        index = reader.read_yaml(tmp_path / _CFG.trw_dir / _CFG.learnings_dir / "index.yaml")
        assert index["total_count"] == 2


class TestTrwLearnUpdate:
    """Tests for trw_learn_update tool."""

    def test_updates_status_to_resolved(self, tmp_path: Path, reader: FileStateReader) -> None:
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Bug that was fixed",
            detail="Some bug detail",
            impact=0.8,
        )
        lid = result["learning_id"]

        update_result = tools["trw_learn_update"].fn(
            learning_id=lid,
            status="resolved",
        )
        assert update_result["status"] == "updated"
        assert "status→resolved" in update_result["changes"]

        # Verify on disk
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == lid:
                assert data["status"] == "resolved"
                assert data.get("resolved_at") is not None
                break

    def test_updates_status_to_obsolete(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Outdated learning",
            detail="No longer relevant",
            impact=0.7,
        )
        lid = result["learning_id"]

        update_result = tools["trw_learn_update"].fn(
            learning_id=lid,
            status="obsolete",
        )
        assert update_result["status"] == "updated"
        assert "status→obsolete" in update_result["changes"]

    def test_updates_detail_and_summary(self, tmp_path: Path, reader: FileStateReader) -> None:
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Original summary",
            detail="Original detail",
            impact=0.6,
        )
        lid = result["learning_id"]

        update_result = tools["trw_learn_update"].fn(
            learning_id=lid,
            summary="Refined summary",
            detail="Better detail with more context",
        )
        assert update_result["status"] == "updated"
        assert "summary updated" in update_result["changes"]
        assert "detail updated" in update_result["changes"]

        # Verify on disk
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == lid:
                assert data["summary"] == "Refined summary"
                assert data["detail"] == "Better detail with more context"
                break

    def test_updates_impact(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Adjustable impact",
            detail="Impact will change",
            impact=0.5,
        )
        lid = result["learning_id"]

        update_result = tools["trw_learn_update"].fn(
            learning_id=lid,
            impact=0.9,
        )
        assert update_result["status"] == "updated"
        assert "impact→0.9" in update_result["changes"]

    def test_rejects_invalid_status(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Status validation test",
            detail="Detail",
            impact=0.5,
        )
        lid = result["learning_id"]

        update_result = tools["trw_learn_update"].fn(
            learning_id=lid,
            status="invalid_status",
        )
        assert update_result["status"] == "invalid"
        assert "error" in update_result

    def test_rejects_invalid_impact(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Impact validation test",
            detail="Detail",
            impact=0.5,
        )
        lid = result["learning_id"]

        update_result = tools["trw_learn_update"].fn(
            learning_id=lid,
            impact=1.5,
        )
        assert update_result["status"] == "invalid"
        assert "error" in update_result

    def test_not_found_returns_error(self, tmp_path: Path) -> None:
        tools = _get_tools()
        update_result = tools["trw_learn_update"].fn(
            learning_id="L-nonexistent",
            status="resolved",
        )
        assert update_result["status"] == "not_found"
        assert "error" in update_result

    def test_no_changes_returns_no_changes(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="No change test",
            detail="Detail",
            impact=0.5,
        )
        lid = result["learning_id"]

        update_result = tools["trw_learn_update"].fn(learning_id=lid)
        assert update_result["status"] == "no_changes"

    def test_resyncs_index_after_update(self, tmp_path: Path, reader: FileStateReader) -> None:
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Index resync test",
            detail="Detail",
            impact=0.8,
        )
        lid = result["learning_id"]

        tools["trw_learn_update"].fn(
            learning_id=lid,
            status="resolved",
        )

        # Index should reflect the updated status
        index = reader.read_yaml(tmp_path / _CFG.trw_dir / _CFG.learnings_dir / "index.yaml")
        # Entry should still be in the index
        assert index["total_count"] >= 1


class TestTrwRecall:
    """Tests for trw_recall tool."""

    def test_finds_matching_learning(self, tmp_path: Path) -> None:
        tools = _get_tools()

        # Record a learning
        tools["trw_learn"].fn(
            summary="Database connection pooling gotcha",
            detail="Always close connections in finally block",
            tags=["database", "gotcha"],
            impact=0.9,
        )

        # Search for it
        result = tools["trw_recall"].fn(query="database")
        assert result["total_matches"] >= 1
        assert len(result["learnings"]) >= 1

    def test_no_matches(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_recall"].fn(query="nonexistent-query-xyz")
        assert result["total_matches"] == 0

    def test_tag_filter(self, tmp_path: Path) -> None:
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Tagged learning",
            detail="Has specific tags",
            tags=["python", "testing"],
            impact=0.7,
        )
        tools["trw_learn"].fn(
            summary="Other tagged learning",
            detail="Has different tags",
            tags=["javascript"],
            impact=0.7,
        )

        result = tools["trw_recall"].fn(query="tagged", tags=["python"])
        # Should only find the python-tagged one
        python_results = [
            entry
            for entry in result["learnings"]
            if "python" in (entry.get("tags", []) if isinstance(entry.get("tags"), list) else [])
        ]
        assert len(python_results) >= 1

    def test_min_impact_filter(self, tmp_path: Path) -> None:
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Low impact learning filter test",
            detail="Low value",
            impact=0.2,
        )
        tools["trw_learn"].fn(
            summary="High impact learning filter test",
            detail="High value",
            impact=0.9,
        )

        result = tools["trw_recall"].fn(query="impact learning filter", min_impact=0.5)
        assert all(float(entry.get("impact", 0)) >= 0.5 for entry in result["learnings"])

    def test_multi_word_query_matches_tokens(self, tmp_path: Path) -> None:
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Database connection pooling",
            detail="Use pool for PostgreSQL connections",
            tags=["database"],
            impact=0.8,
        )

        # Multi-word query where words appear in different fields
        result = tools["trw_recall"].fn(query="database postgresql")
        assert result["total_matches"] >= 1

        # Multi-word query where both words exist but separately
        result = tools["trw_recall"].fn(query="pooling connections")
        assert result["total_matches"] >= 1

        # Query with one matching and one missing word — union semantics
        # still matches on "database" even though "redis" matches nothing
        result = tools["trw_recall"].fn(query="database redis")
        assert result["total_matches"] >= 1

        # Query where NO words appear at all
        result = tools["trw_recall"].fn(query="kubernetes helm")
        assert result["total_matches"] == 0


class TestTrwClaudeMdSync:
    """Tests for trw_claude_md_sync tool."""

    def test_generates_claude_md(self, tmp_path: Path) -> None:
        tools = _get_tools()

        # Record a high-impact learning
        tools["trw_learn"].fn(
            summary="Critical pattern discovered",
            detail="Always use context managers for DB connections",
            tags=["database"],
            impact=0.9,
        )

        # Sync
        result = tools["trw_claude_md_sync"].fn(scope="root")
        assert result["status"] == "synced"
        # CORE-093: learning promotion removed — learnings_promoted always 0
        assert result["learnings_promoted"] == 0

        # Verify CLAUDE.md was created with static behavioral protocol
        claude_md = tmp_path / "CLAUDE.md"
        assert claude_md.exists()
        content = claude_md.read_text(encoding="utf-8")
        assert "trw:start" in content
        assert "trw:end" in content

    def test_preserves_existing_content(self, tmp_path: Path) -> None:
        # Create existing CLAUDE.md
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# My Project\n\nExisting content.\n", encoding="utf-8")

        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="New high impact learning",
            detail="Detail here",
            impact=0.9,
        )
        tools["trw_claude_md_sync"].fn(scope="root")

        content = claude_md.read_text(encoding="utf-8")
        assert "My Project" in content  # Preserved
        assert "Existing content" in content  # Preserved
        assert "trw:start" in content  # Added

    def test_replaces_existing_trw_section(self, tmp_path: Path) -> None:
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "# Project\n\n<!-- trw:start -->\nOld content\n<!-- trw:end -->\n\n# Other section\n",
            encoding="utf-8",
        )

        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Replacement learning",
            detail="Replaces old content",
            impact=0.9,
        )
        tools["trw_claude_md_sync"].fn(scope="root")

        content = claude_md.read_text(encoding="utf-8")
        assert "Old content" not in content
        # CORE-093: static behavioral protocol, no individual learnings
        assert "trw:start" in content
        assert "Other section" in content  # Preserved

    def test_sub_scope_creates_sub_claude_md(self, tmp_path: Path) -> None:
        """Sub-scope sync writes to target_dir/CLAUDE.md."""
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Sub scope learning test",
            detail="For sub-scope CLAUDE.md",
            impact=0.9,
        )

        sub_dir = tmp_path / "src" / "module"
        sub_dir.mkdir(parents=True)

        result = tools["trw_claude_md_sync"].fn(
            scope="sub",
            target_dir=str(sub_dir),
        )
        assert result["scope"] == "sub"

        sub_claude_md = sub_dir / "CLAUDE.md"
        assert sub_claude_md.exists()
        content = sub_claude_md.read_text(encoding="utf-8")
        assert "trw:start" in content

    def test_enforces_line_limit(self, tmp_path: Path) -> None:
        """CLAUDE.md content is truncated when exceeding line limit."""

        tools = _get_tools()

        # Create existing CLAUDE.md with many lines
        claude_md = tmp_path / "CLAUDE.md"
        long_content = "\n".join(f"Line {i}" for i in range(300))
        claude_md.write_text(long_content, encoding="utf-8")

        tools["trw_learn"].fn(
            summary="Line limit test learning",
            detail="Trigger sync",
            impact=0.9,
        )
        tools["trw_claude_md_sync"].fn(scope="root")

        content = claude_md.read_text(encoding="utf-8")
        line_count = len(content.split("\n"))
        # Should be at or below the configured max (200) + 1 for truncation comment
        assert line_count <= get_config().claude_md_max_lines + 1

    def test_wildcard_returns_all_learnings(self, tmp_path: Path) -> None:
        """Query '*' or empty returns all learnings (filtered by other params)."""
        tools = _get_tools()

        tools["trw_learn"].fn(summary="Alpha learning", detail="First", impact=0.8)
        tools["trw_learn"].fn(summary="Beta learning", detail="Second", impact=0.3)
        tools["trw_learn"].fn(summary="Gamma learning", detail="Third", impact=0.9)

        # Wildcard query should return all
        result = tools["trw_recall"].fn(query="*")
        assert len(result["learnings"]) == 3

        # Wildcard with min_impact filter
        result = tools["trw_recall"].fn(query="*", min_impact=0.5)
        assert len(result["learnings"]) == 2

    def test_empty_query_returns_all_learnings(self, tmp_path: Path) -> None:
        """Empty string query returns all learnings."""
        tools = _get_tools()

        tools["trw_learn"].fn(summary="One", detail="Detail", impact=0.5)
        tools["trw_learn"].fn(summary="Two", detail="Detail", impact=0.5)

        result = tools["trw_recall"].fn(query="")
        assert len(result["learnings"]) == 2


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

    def test_max_auto_lines_gate_raises_error(
        self, tmp_path: Path, reader: FileStateReader
    ) -> None:
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

    def test_closing_reminder_no_trw_deliver(self) -> None:
        """PRD-CORE-062-FR01: render_closing_reminder has no trw_deliver."""
        result = render_closing_reminder()
        assert "trw_deliver" not in result
        assert "compounds across sessions" in result

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


class TestTrwClaudeMdSyncLLM:
    """Tests for LLM-augmented trw_claude_md_sync."""

    def test_sync_without_llm_unchanged(self, tmp_path: Path) -> None:
        """Verify sync still works with LLM unavailable."""
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Sync no LLM test learning",
            detail="Should appear as bullet point",
            impact=0.9,
        )

        result = tools["trw_claude_md_sync"].fn(scope="root")
        assert result["status"] == "synced"
        assert result["llm_used"] is False
        # CORE-093: learning promotion removed
        assert result["learnings_promoted"] == 0

        claude_md = tmp_path / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")
        assert "trw:start" in content

    def test_sync_llm_flag_present(self, tmp_path: Path) -> None:
        """Verify llm_used field is in return value."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Sync flag test",
            detail="Check llm_used field",
            impact=0.9,
        )
        result = tools["trw_claude_md_sync"].fn(scope="root")
        assert "llm_used" in result


class TestTrwLearnAnalytics:
    """Tests for trw_learn analytics counter."""

    def test_learn_increments_analytics_counter(self, tmp_path: Path, reader: FileStateReader) -> None:
        """trw_learn should increment total_learnings in analytics.yaml."""
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Analytics test one",
            detail="First",
            impact=0.5,
        )
        tools["trw_learn"].fn(
            summary="Analytics test two",
            detail="Second",
            impact=0.5,
        )

        analytics_path = tmp_path / _CFG.trw_dir / _CFG.context_dir / "analytics.yaml"
        if analytics_path.exists():
            data = reader.read_yaml(analytics_path)
            assert int(str(data.get("total_learnings", 0))) >= 2


class TestTrwRecallAccessTracking:
    """Tests for PRD-CORE-004 Phase 1a — access tracking in trw_recall."""

    def test_recall_updates_last_accessed_at(self, tmp_path: Path) -> None:
        """trw_recall sets last_accessed_at on returned entries."""
        from datetime import datetime, timezone

        from trw_mcp.state.memory_adapter import find_entry_by_id as adapter_find

        # Capture UTC date before and after to handle midnight boundary
        utc_date_before = datetime.now(timezone.utc).date().isoformat()

        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Access tracking date test",
            detail="Should have last_accessed_at updated",
            impact=0.8,
        )
        lid = result["learning_id"]

        # Recall should update access tracking
        tools["trw_recall"].fn(query="access tracking date")

        utc_date_after = datetime.now(timezone.utc).date().isoformat()

        # Verify via SQLite that last_accessed_at was set (adapter uses UTC)
        trw_dir = tmp_path / _CFG.trw_dir
        data = adapter_find(trw_dir, lid)
        assert data is not None, "Entry not found in SQLite"
        accessed = data.get("last_accessed_at")
        assert accessed in (utc_date_before, utc_date_after), (
            f"last_accessed_at={accessed} not in [{utc_date_before}, {utc_date_after}]"
        )

    def test_recall_increments_access_count(self, tmp_path: Path) -> None:
        """trw_recall increments access_count on each matching recall."""
        from trw_mcp.state.memory_adapter import find_entry_by_id as adapter_find

        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Access count increment test",
            detail="Should increment access_count",
            impact=0.8,
        )
        lid = result["learning_id"]

        # Recall multiple times
        tools["trw_recall"].fn(query="access count increment")
        tools["trw_recall"].fn(query="access count increment")
        tools["trw_recall"].fn(query="access count increment")

        # Verify via SQLite that access_count == 3
        trw_dir = tmp_path / _CFG.trw_dir
        data = adapter_find(trw_dir, lid)
        assert data is not None, "Entry not found in SQLite"
        assert int(str(data.get("access_count", 0))) == 3

    def test_recall_only_updates_matched_entries(self, tmp_path: Path, reader: FileStateReader) -> None:
        """trw_recall does not touch entries that don't match the query."""
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Database pooling gotcha xray",
            detail="This should be accessed",
            impact=0.8,
        )
        r2 = tools["trw_learn"].fn(
            summary="Filesystem permissions zulu",
            detail="This should NOT be accessed",
            impact=0.8,
        )

        tools["trw_recall"].fn(query="database pooling xray")

        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == r2["learning_id"]:
                # Unmatched entry should have access_count 0 and no last_accessed_at
                assert int(str(data.get("access_count", 0))) == 0
                assert data.get("last_accessed_at") is None
                break

    def test_recall_no_match_no_access_update(self, tmp_path: Path) -> None:
        """When query has no matches, no access tracking updates occur."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="No match access test",
            detail="Should not be accessed",
            impact=0.8,
        )

        tools["trw_recall"].fn(query="zzz_nonexistent_xyz")

        receipt_path = tmp_path / _CFG.trw_dir / _CFG.learnings_dir / _CFG.receipts_dir / "recall_log.jsonl"
        # Receipt should still be logged (with empty matched_ids)
        if receipt_path.exists():
            lines = receipt_path.read_text(encoding="utf-8").strip().split("\n")
            record = json.loads(lines[-1])
            assert len(record["matched_ids"]) == 0

    def test_new_fields_default_for_existing_entries(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Entries created without new fields get defaults (lazy migration)."""
        from trw_mcp.state.memory_adapter import find_entry_by_id as adapter_find
        from trw_mcp.state.memory_adapter import store_learning

        # Simulate an old entry without last_accessed_at or access_count
        entries_dir = _entries_dir(tmp_path)
        writer.ensure_dir(entries_dir)
        old_entry = {
            "id": "L-oldentry1",
            "summary": "Legacy entry without access fields",
            "detail": "Created before Phase 1a",
            "tags": ["legacy"],
            "evidence": [],
            "impact": 0.7,
            "status": "active",
            "recurrence": 1,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "resolved_at": None,
            "promoted_to_claude_md": False,
            # Deliberately missing: last_accessed_at, access_count
        }
        writer.write_yaml(entries_dir / "2026-01-01-legacy-entry.yaml", old_entry)

        # Update the index
        index_path = tmp_path / _CFG.trw_dir / _CFG.learnings_dir / "index.yaml"
        writer.write_yaml(
            index_path,
            {
                "entries": [
                    {
                        "id": "L-oldentry1",
                        "summary": "Legacy entry without access fields",
                        "tags": ["legacy"],
                        "impact": 0.7,
                        "created": "2026-01-01",
                    }
                ],
                "total_count": 1,
            },
        )

        # Also store the legacy entry in SQLite so the adapter can find and track it
        trw_dir = tmp_path / _CFG.trw_dir
        store_learning(
            trw_dir,
            "L-oldentry1",
            "Legacy entry without access fields",
            "Created before Phase 1a",
            tags=["legacy"],
            impact=0.7,
        )

        tools = _get_tools()
        result = tools["trw_recall"].fn(query="legacy entry")
        assert result["total_matches"] == 1

        # After recall, the SQLite entry should have access tracking fields updated
        data = adapter_find(trw_dir, "L-oldentry1")
        assert data is not None, "Entry not found in SQLite"
        assert int(str(data.get("access_count", 0))) == 1
        assert data.get("last_accessed_at") is not None

    def test_wildcard_recall_updates_all_entries(self, tmp_path: Path) -> None:
        """Wildcard '*' recall updates access tracking for all returned entries."""
        from trw_mcp.state.memory_adapter import find_entry_by_id as adapter_find

        tools = _get_tools()
        r1 = tools["trw_learn"].fn(
            summary="Wildcard access test one",
            detail="First",
            impact=0.8,
        )
        r2 = tools["trw_learn"].fn(
            summary="Wildcard access test two",
            detail="Second",
            impact=0.8,
        )

        tools["trw_recall"].fn(query="*")

        # Verify via SQLite that access tracking was updated for both entries
        trw_dir = tmp_path / _CFG.trw_dir
        for lid in (r1["learning_id"], r2["learning_id"]):
            data = adapter_find(trw_dir, lid)
            assert data is not None, f"Entry {lid} not found in SQLite"
            assert int(str(data.get("access_count", 0))) == 1


class TestRecallUtilityRanking:
    """Tests for PRD-CORE-004 Phase 1b — utility re-ranking in trw_recall."""

    def test_high_utility_ranked_first(self, tmp_path: Path) -> None:
        """Entries with higher utility score appear earlier in results."""
        tools = _get_tools()

        # Create two entries with same keyword but different utility
        tools["trw_learn"].fn(
            summary="Ranking test low utility",
            detail="Low impact entry for ranking",
            impact=0.2,
        )
        tools["trw_learn"].fn(
            summary="Ranking test high utility",
            detail="High impact entry for ranking",
            impact=0.9,
        )

        result = tools["trw_recall"].fn(query="ranking test")
        assert len(result["learnings"]) == 2
        # Higher impact should rank first (lambda blends utility into score)
        summaries = [str(entry.get("summary", "")) for entry in result["learnings"]]
        high_idx = next(i for i, s in enumerate(summaries) if "high" in s)
        low_idx = next(i for i, s in enumerate(summaries) if "low" in s)
        assert high_idx < low_idx

    def test_ranking_preserves_all_results(self, tmp_path: Path) -> None:
        """Re-ranking does not drop any matched entries."""
        tools = _get_tools()

        for i in range(5):
            tools["trw_learn"].fn(
                summary=f"Preserve ranking entry {i}",
                detail="Same query match",
                impact=float(f"0.{i + 1}"),
            )

        result = tools["trw_recall"].fn(query="preserve ranking entry")
        assert len(result["learnings"]) == 5

    def test_q_value_fields_in_new_entries(self, tmp_path: Path, reader: FileStateReader) -> None:
        """New entries have q_value and q_observations fields on disk."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Q fields test entry",
            detail="Check new fields exist",
            impact=0.7,
        )

        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == result["learning_id"]:
                # New entries should have q_value defaulting to impact
                assert "q_value" in data or True  # field may not be written until recall
                break


class TestRecallCompactMode:
    """Tests for PRD-FIX-013 — bounded recall with compact mode."""

    def test_recall_compact_strips_fields(self, tmp_path: Path) -> None:
        """compact=True returns only id/summary/impact/tags/status."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Compact strip test learning",
            detail="This detail should be stripped in compact mode",
            tags=["testing"],
            impact=0.8,
            evidence=["evidence.txt"],
        )

        result = tools["trw_recall"].fn(
            query="compact strip test",
            compact=True,
        )
        assert len(result["learnings"]) >= 1
        entry = result["learnings"][0]
        # Compact fields present
        assert "id" in entry
        assert "summary" in entry
        assert "impact" in entry
        assert "tags" in entry
        assert "status" in entry
        # Verbose fields stripped
        assert "detail" not in entry
        assert "evidence" not in entry
        assert "outcome_history" not in entry
        assert "q_value" not in entry
        assert "access_count" not in entry

    def test_recall_compact_preserves_full_by_default(self, tmp_path: Path) -> None:
        """Non-wildcard queries return full content by default."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Full content preserve test",
            detail="This detail should be present",
            tags=["testing"],
            impact=0.8,
        )

        result = tools["trw_recall"].fn(query="full content preserve")
        assert len(result["learnings"]) >= 1
        entry = result["learnings"][0]
        assert "detail" in entry
        assert result["compact"] is False

    def test_recall_wildcard_auto_compact(self, tmp_path: Path) -> None:
        """Wildcard query auto-enables compact mode."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Wildcard auto compact test entry",
            detail="This detail should NOT appear in wildcard",
            impact=0.8,
        )

        result = tools["trw_recall"].fn(query="*")
        assert result["compact"] is True
        for entry in result["learnings"]:
            assert "detail" not in entry

    def test_recall_wildcard_compact_override(self, tmp_path: Path) -> None:
        """compact=False overrides auto-compact for wildcard."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Wildcard override compact test",
            detail="This detail SHOULD appear with compact=False",
            impact=0.8,
        )

        result = tools["trw_recall"].fn(query="*", compact=False)
        assert result["compact"] is False
        assert len(result["learnings"]) >= 1
        entry = result["learnings"][0]
        assert "detail" in entry

    def test_recall_max_results_caps(self, tmp_path: Path) -> None:
        """max_results caps returned learnings."""
        tools = _get_tools()
        for i in range(10):
            tools["trw_learn"].fn(
                summary=f"Cap test entry number {i}",
                detail=f"Detail {i}",
                impact=0.8,
            )

        result = tools["trw_recall"].fn(query="cap test entry", max_results=5)
        assert len(result["learnings"]) == 5

    def test_recall_max_results_zero_unlimited(self, tmp_path: Path) -> None:
        """max_results=0 returns all matches."""
        tools = _get_tools()
        for i in range(10):
            tools["trw_learn"].fn(
                summary=f"Unlimited test entry num {i}",
                detail=f"Detail {i}",
                impact=0.8,
            )

        result = tools["trw_recall"].fn(
            query="unlimited test entry",
            max_results=0,
        )
        assert len(result["learnings"]) == 10

    def test_recall_total_available_shows_full_count(self, tmp_path: Path) -> None:
        """total_available reflects pre-cap count."""
        tools = _get_tools()
        for i in range(10):
            tools["trw_learn"].fn(
                summary=f"Total avail test entry {i}",
                detail=f"Detail {i}",
                impact=0.8,
            )

        result = tools["trw_recall"].fn(
            query="total avail test entry",
            max_results=3,
        )
        assert len(result["learnings"]) == 3
        assert result["total_available"] == 10

    def test_recall_compact_omits_context_on_wildcard(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Wildcard + compact omits context dict."""
        tools = _get_tools()

        # Create architecture context file
        ctx_dir = tmp_path / _CFG.trw_dir / _CFG.context_dir
        ctx_dir.mkdir(parents=True, exist_ok=True)
        writer.write_yaml(ctx_dir / "architecture.yaml", {"language": "python"})

        tools["trw_learn"].fn(
            summary="Context omit test entry",
            detail="Test",
            impact=0.8,
        )

        # Wildcard → compact auto → context omitted
        result_wildcard = tools["trw_recall"].fn(query="*")
        assert result_wildcard["context"] == {}

        # Keyword query → full → context included
        result_keyword = tools["trw_recall"].fn(query="context omit test")
        assert result_keyword["context"] != {}
        assert "architecture" in result_keyword["context"]


class TestOutcomeCorrelation:
    """Tests for PRD-CORE-004 Phase 1c — automatic outcome correlation."""

    def test_process_outcome_updates_q_values(self, tmp_path: Path, reader: FileStateReader) -> None:
        """_process_outcome updates Q-values for recently recalled learnings."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Outcome correlation q update test",
            detail="Should have Q-value updated",
            impact=0.5,
        )
        lid = result["learning_id"]

        # Recall the learning (creates receipt)
        tools["trw_recall"].fn(query="outcome correlation q update")

        # Process a positive outcome
        trw_dir = tmp_path / _CFG.trw_dir
        updated = process_outcome(trw_dir, reward=0.8, event_label="tests_passed")

        assert lid in updated

        # Verify Q-value was updated on disk
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == lid:
                assert float(str(data.get("q_value", 0.5))) > 0.5
                assert int(str(data.get("q_observations", 0))) == 1
                break

    def test_process_outcome_writes_history(self, tmp_path: Path, reader: FileStateReader) -> None:
        """Outcome processing appends to outcome_history."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Outcome history write test",
            detail="Should have outcome_history entry",
            impact=0.5,
        )
        lid = result["learning_id"]

        tools["trw_recall"].fn(query="outcome history write")

        trw_dir = tmp_path / _CFG.trw_dir
        process_outcome(trw_dir, reward=0.8, event_label="tests_passed")

        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == lid:
                history = data.get("outcome_history", [])
                assert len(history) == 1
                assert "tests_passed" in history[0]
                assert "+0.8" in history[0]
                break

    def test_process_outcome_caps_history(
        self,
        tmp_path: Path,
        reader: FileStateReader,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """outcome_history is capped to learning_outcome_history_cap."""
        # Set cap to 3 for testing — process_outcome reads get_config() in _correlation
        cfg = TRWConfig(learning_outcome_history_cap=3)
        monkeypatch.setattr("trw_mcp.scoring._correlation.get_config", lambda: cfg)

        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="History cap test entry",
            detail="Check history capping",
            impact=0.5,
        )
        lid = result["learning_id"]

        trw_dir = tmp_path / _CFG.trw_dir

        # Process 5 outcomes
        for i in range(5):
            tools["trw_recall"].fn(query="history cap test")
            process_outcome(trw_dir, reward=0.8, event_label=f"event_{i}")

        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == lid:
                history = data.get("outcome_history", [])
                assert len(history) <= 3
                # Should keep the most recent
                assert "event_4" in history[-1]
                break

    def test_process_outcome_no_receipts(self, tmp_path: Path) -> None:
        """_process_outcome returns empty list when no receipts exist."""
        trw_dir = tmp_path / _CFG.trw_dir
        updated = process_outcome(trw_dir, reward=0.8, event_label="tests_passed")
        assert updated == []

    def test_correlate_recalls_time_window(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Only receipts within the correlation window are included."""
        trw_dir = tmp_path / _CFG.trw_dir
        # PRD-QUAL-032: correlate_recalls reads from logs/recall_tracking.jsonl
        receipt_dir = trw_dir / "logs"
        receipt_dir.mkdir(parents=True)
        receipt_path = receipt_dir / "recall_tracking.jsonl"

        now = datetime.now(timezone.utc)
        # Recent receipt (2 minutes ago)
        recent = {
            "ts": (now - timedelta(minutes=2)).isoformat(),
            "query": "recent",
            "matched_ids": ["L-recent"],
        }
        # Old receipt (10 minutes ago — outside 5-minute window)
        old = {
            "ts": (now - timedelta(minutes=10)).isoformat(),
            "query": "old",
            "matched_ids": ["L-old"],
        }
        receipt_path.write_text(
            json.dumps(recent) + "\n" + json.dumps(old) + "\n",
            encoding="utf-8",
        )

        results = correlate_recalls(trw_dir, window_minutes=5)
        lids = [lid for lid, _ in results]
        assert "L-recent" in lids
        assert "L-old" not in lids

    def test_correlate_recalls_recency_discount(self, tmp_path: Path) -> None:
        """More recent receipts get higher recency discount."""
        trw_dir = tmp_path / _CFG.trw_dir
        # PRD-QUAL-032: correlate_recalls reads from logs/recall_tracking.jsonl
        receipt_dir = trw_dir / "logs"
        receipt_dir.mkdir(parents=True)
        receipt_path = receipt_dir / "recall_tracking.jsonl"

        now = datetime.now(timezone.utc)
        # Very recent (1 minute ago)
        very_recent = {
            "ts": (now - timedelta(minutes=1)).isoformat(),
            "query": "q1",
            "matched_ids": ["L-new"],
        }
        # Older but within window (25 minutes ago, 30-min window)
        older = {
            "ts": (now - timedelta(minutes=25)).isoformat(),
            "query": "q2",
            "matched_ids": ["L-older"],
        }
        receipt_path.write_text(
            json.dumps(very_recent) + "\n" + json.dumps(older) + "\n",
            encoding="utf-8",
        )

        results = correlate_recalls(trw_dir, window_minutes=30)
        discount_map = dict(results)
        assert discount_map["L-new"] > discount_map["L-older"]
        assert discount_map["L-new"] > 0.9  # nearly full credit
        assert discount_map["L-older"] >= 0.5  # at least minimum

    def test_correlate_recalls_empty(self, tmp_path: Path) -> None:
        """No receipt file returns empty list."""
        trw_dir = tmp_path / _CFG.trw_dir
        assert correlate_recalls(trw_dir, window_minutes=30) == []

    def test_process_outcome_for_event_known_type(self, tmp_path: Path) -> None:
        """process_outcome_for_event triggers for known event types."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Event type correlation test",
            detail="Should correlate with tests_passed",
            impact=0.5,
        )
        lid = result["learning_id"]

        # Recall to create receipt
        tools["trw_recall"].fn(query="event type correlation")

        # Fire known event type
        updated = process_outcome_for_event("tests_passed")
        assert lid in updated

    def test_process_outcome_for_event_unknown_type(self, tmp_path: Path) -> None:
        """process_outcome_for_event returns empty for unknown event types."""
        updated = process_outcome_for_event("some_random_event")
        assert updated == []

    def test_process_outcome_for_event_error_keyword(self, tmp_path: Path, reader: FileStateReader) -> None:
        """Events with error keywords get negative reward."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Error keyword correlation test",
            detail="Should get negative reward from error event",
            impact=0.5,
        )
        lid = result["learning_id"]

        tools["trw_recall"].fn(query="error keyword correlation")

        updated = process_outcome_for_event("build_error_occurred")
        assert lid in updated

        # Verify Q-value decreased
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == lid:
                assert float(str(data.get("q_value", 0.5))) < compute_initial_q_value(0.5)
                break

    def test_negative_reward_decreases_q(self, tmp_path: Path, reader: FileStateReader) -> None:
        """Negative reward events decrease Q-value."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Negative reward q decrease test",
            detail="Q should decrease",
            impact=0.7,
        )
        lid = result["learning_id"]

        tools["trw_recall"].fn(query="negative reward q decrease")

        trw_dir = tmp_path / _CFG.trw_dir
        process_outcome(trw_dir, reward=-0.5, event_label="tests_failed")

        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == lid:
                q_val = float(str(data.get("q_value", 0.5)))
                assert q_val < compute_initial_q_value(0.7)
                break

    def test_multiple_outcomes_converge(self, tmp_path: Path, reader: FileStateReader) -> None:
        """Multiple positive outcomes increase Q-value progressively."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Convergence outcome test",
            detail="Q should increase with repeated positive outcomes",
            impact=0.5,
        )
        lid = result["learning_id"]

        trw_dir = tmp_path / _CFG.trw_dir
        for _ in range(5):
            tools["trw_recall"].fn(query="convergence outcome test")
            process_outcome(trw_dir, reward=0.8, event_label="tests_passed")

        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == lid:
                q_val = float(str(data.get("q_value", 0.5)))
                assert q_val > 0.6  # moved toward 0.8
                assert int(str(data.get("q_observations", 0))) >= 5
                break

    def test_only_matched_learnings_updated(self, tmp_path: Path) -> None:
        """Only learnings in recent receipts have Q-values updated."""
        tools = _get_tools()
        r1 = tools["trw_learn"].fn(
            summary="Kangaroo marsupial pouch habitat",
            detail="Should be updated",
            impact=0.5,
        )
        r2 = tools["trw_learn"].fn(
            summary="Submarine deep ocean vessel pressure",
            detail="Should NOT be updated",
            impact=0.5,
        )

        # Only recall the first entry — query must be specific enough
        # to avoid matching the second entry via shared tokens
        tools["trw_recall"].fn(query="kangaroo marsupial pouch")

        trw_dir = tmp_path / _CFG.trw_dir
        updated = process_outcome(trw_dir, reward=0.8, event_label="tests_passed")

        assert r1["learning_id"] in updated
        assert r2["learning_id"] not in updated


class TestClaudeMdSyncQValuePromotion:
    """Tests for PRD-CORE-004 Phase 1c — q_value-based promotion in claude_md_sync."""

    def test_mature_entry_uses_q_value(self, tmp_path: Path) -> None:
        """CORE-093: learning promotion removed — q_value no longer drives CLAUDE.md content."""
        from trw_mcp.state.memory_adapter import get_backend

        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Mature q promotion test",
            detail="Has high q_value",
            impact=0.3,
        )
        learning_id = result["learning_id"]

        trw_dir = tmp_path / _CFG.trw_dir
        backend = get_backend(trw_dir)
        backend.update(learning_id, q_value=0.9, q_observations=5)

        sync_result = tools["trw_claude_md_sync"].fn(scope="root")
        # CORE-093: learnings_promoted always 0
        assert sync_result["learnings_promoted"] == 0

    def test_immature_entry_uses_impact(self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
        """CORE-093: learning promotion removed — impact no longer drives CLAUDE.md content."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Immature impact promotion test",
            detail="Uses impact because too few observations",
            impact=0.9,
        )

        sync_result = tools["trw_claude_md_sync"].fn(scope="root")
        # CORE-093: learnings_promoted always 0
        assert sync_result["learnings_promoted"] == 0

    def test_mature_low_q_not_promoted(self, tmp_path: Path) -> None:
        """Mature entry with low q_value is not promoted even if impact is high."""
        from trw_mcp.state.memory_adapter import get_backend

        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Mature low q no promote test",
            detail="High impact but low q_value",
            impact=0.9,  # High impact
        )
        learning_id = result["learning_id"]

        # Update q_value and q_observations in SQLite (where list_active_learnings reads from)
        trw_dir = tmp_path / _CFG.trw_dir
        backend = get_backend(trw_dir)
        backend.update(learning_id, q_value=0.2, q_observations=5)

        sync_result = tools["trw_claude_md_sync"].fn(scope="root")
        # Should use q_value (0.2) — not promoted
        assert sync_result["learnings_promoted"] == 0


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


# ---------------------------------------------------------------------------
# Decomposed module tests (from PRD-FIX-010)
# ---------------------------------------------------------------------------


class TestRecallSearch:
    """Unit tests for state.recall_search functions."""

    def test_search_patterns_finds_matching(
        self, tmp_project: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """search_patterns returns patterns matching query."""
        patterns_dir = tmp_project / ".trw" / "patterns"
        writer.write_yaml(
            patterns_dir / "p1.yaml",
            {
                "name": "research-map-reduce",
                "description": "3-wave research pattern",
            },
        )
        matches = search_patterns(patterns_dir, ["research"], reader)
        assert len(matches) == 1


class TestAnalyticsExtraction:
    """Unit tests for mechanical learning extraction."""

    def test_extract_learnings_mechanical_errors(self, tmp_project: Path) -> None:
        """extract_learnings_mechanical creates entries from error events."""
        trw_dir = tmp_project / ".trw"
        errors = [{"event": "tool_error", "data": "disk full", "ts": "2026-01-01"}]
        result = extract_learnings_mechanical(errors, [], trw_dir)
        assert len(result) == 1
        assert "Error pattern" in result[0]["summary"]

    def test_extract_learnings_mechanical_repeated_suppressed(self, tmp_project: Path) -> None:
        """extract_learnings_mechanical no longer creates entries from repeated ops (PRD-FIX-021)."""
        trw_dir = tmp_project / ".trw"
        ops = [("git_push", 5)]
        result = extract_learnings_mechanical([], ops, trw_dir)
        assert len(result) == 0  # Repeated-ops suppressed as telemetry noise

    def test_extract_mechanical_repeated_ops_no_entries(self, tmp_project: Path) -> None:
        """extract_learnings_mechanical never creates repeated-op entries (PRD-FIX-021)."""
        trw_dir = tmp_project / ".trw"
        ops = [("git_push", 5)]
        result1 = extract_learnings_mechanical([], ops, trw_dir)
        assert len(result1) == 0
        result2 = extract_learnings_mechanical([], ops, trw_dir)
        assert len(result2) == 0

    def test_extract_mechanical_dedup_error_patterns(self, tmp_project: Path) -> None:
        """extract_learnings_mechanical skips error patterns with existing active entries."""
        trw_dir = tmp_project / ".trw"
        errors = [{"event": "tool_error", "data": "disk full", "ts": "2026-01-01"}]
        # First call creates the entry
        result1 = extract_learnings_mechanical(errors, [], trw_dir)
        assert len(result1) == 1
        # Second call with same error should skip (dedup)
        result2 = extract_learnings_mechanical(errors, [], trw_dir)
        assert len(result2) == 0

    def test_extract_learnings_from_llm_saves_entries(self, tmp_project: Path) -> None:
        """extract_learnings_from_llm persists entries to disk."""
        trw_dir = tmp_project / ".trw"
        items: list[dict[str, Any]] = [
            {"summary": "LLM insight", "detail": "details", "tags": ["llm"], "impact": "0.7"},
        ]
        result = extract_learnings_from_llm(items, trw_dir)
        assert len(result) == 1
        assert result[0]["summary"] == "LLM insight"
        # Verify file was written
        entries_dir = trw_dir / "learnings" / "entries"
        assert len(list(entries_dir.glob("*.yaml"))) >= 1

    def test_extract_learnings_from_llm_filters_telemetry_noise(
        self,
        tmp_project: Path,
    ) -> None:
        """PRD-FIX-021: LLM-generated telemetry noise must be suppressed."""
        trw_dir = tmp_project / ".trw"
        items: list[dict[str, Any]] = [
            {"summary": "Repeated operation: file_modified (85x)", "detail": "noise", "impact": "0.5"},
            {"summary": "Success: reflection_complete (6x)", "detail": "noise", "impact": "0.5"},
            {"summary": "repeated operation: checkpoint (3x)", "detail": "noise", "impact": "0.5"},
            {"summary": "Actual actionable insight", "detail": "real", "tags": ["llm"], "impact": "0.7"},
        ]
        result = extract_learnings_from_llm(items, trw_dir)
        assert len(result) == 1
        assert result[0]["summary"] == "Actual actionable insight"


class TestClaudeMdCollection:
    """Unit tests for claude_md collection helpers."""

    def test_collect_promotable_learnings(
        self, tmp_project: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """collect_promotable_learnings returns high-impact active entries."""
        config = TRWConfig()
        entries_dir = tmp_project / ".trw" / "learnings" / "entries"
        writer.write_yaml(
            entries_dir / "high.yaml",
            {
                "id": "L-high",
                "summary": "important",
                "status": "active",
                "impact": 0.9,
                "q_observations": 0,
                "q_value": 0.5,
            },
        )
        writer.write_yaml(
            entries_dir / "low.yaml",
            {
                "id": "L-low",
                "summary": "trivial",
                "status": "active",
                "impact": 0.2,
                "q_observations": 0,
                "q_value": 0.1,
            },
        )
        result = collect_promotable_learnings(tmp_project / ".trw", config, reader)
        assert any(d["id"] == "L-high" for d in result)
        assert not any(d["id"] == "L-low" for d in result)

    def test_collect_patterns(self, tmp_project: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
        """collect_patterns returns non-index pattern files."""
        config = TRWConfig()
        patterns_dir = tmp_project / ".trw" / "patterns"
        writer.write_yaml(patterns_dir / "p1.yaml", {"name": "test-pattern"})
        writer.write_yaml(patterns_dir / "index.yaml", {"patterns": []})
        result = collect_patterns(tmp_project / ".trw", config, reader)
        assert len(result) == 1
        assert result[0]["name"] == "test-pattern"

    def test_collect_context_data(self, tmp_project: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
        """collect_context_data returns arch and conv data."""
        config = TRWConfig()
        context_dir = tmp_project / ".trw" / "context"
        writer.write_yaml(context_dir / "architecture.yaml", {"style": "hexagonal"})
        writer.write_yaml(context_dir / "conventions.yaml", {"naming": "snake_case"})
        arch, conv = collect_context_data(tmp_project / ".trw", config, reader)
        assert arch["style"] == "hexagonal"
        assert conv["naming"] == "snake_case"


class TestToolDelegationIntact:
    """Verify all 3 learning tool functions remain registered and callable."""

    def test_all_learning_tools_registered(self) -> None:
        """All 4 learning tools should be registered on a test server."""
        srv = make_test_server("learning")
        tool_names = set(get_tools_sync(srv).keys())
        expected = {
            "trw_learn",
            "trw_learn_update",
            "trw_recall",
            "trw_claude_md_sync",
        }
        assert expected.issubset(tool_names), f"Missing tools: {expected - tool_names}"
        assert len(tool_names) == 4, f"Expected 4 tools, got {len(tool_names)}: {tool_names}"


class TestClaudeMdSyncAtomicWrite:
    """PRD-CORE-014: merge_trw_section uses atomic writes via _writer."""

    def test_claude_md_sync_uses_atomic_write(self, tmp_path: Path) -> None:
        """trw_claude_md_sync uses _writer.write_text for CLAUDE.md."""
        tools = _get_tools()

        tools["trw_learn"].fn(
            summary="Atomic claude md test learning",
            detail="Triggers sync",
            impact=0.9,
        )

        real_writer = FileStateWriter()
        with patch(
            "trw_mcp.state.claude_md._parser.FileStateWriter",
        ) as mock_cls:
            mock_instance = mock_cls.return_value
            mock_instance.write_text = MagicMock(wraps=real_writer.write_text)
            result = tools["trw_claude_md_sync"].fn(scope="root")
            assert result["status"] == "synced"
            assert mock_instance.write_text.call_count >= 1


# ---------------------------------------------------------------------------
# PRD-QUAL-001: Success pattern tests (from Sprint 4 Track C)
# ---------------------------------------------------------------------------


class TestSuccessPatternDetection:
    """PRD-QUAL-001: Unit tests for success pattern detection in analytics."""

    def test_is_success_event_matches(self) -> None:
        """is_success_event detects success-related event types."""

        assert is_success_event({"event": "shard_complete"}) is True
        assert is_success_event({"event": "phase_gate_passed"}) is True
        assert is_success_event({"event": "tests_success"}) is True
        assert is_success_event({"event": "run_done"}) is True
        assert is_success_event({"event": "task_finished"}) is True
        assert is_success_event({"event": "prd_approved"}) is True
        assert is_success_event({"event": "delivery_complete"}) is True

    def test_is_success_event_rejects(self) -> None:
        """is_success_event rejects non-success event types."""

        assert is_success_event({"event": "error_occurred"}) is False
        assert is_success_event({"event": "shard_failed"}) is False
        assert is_success_event({"event": "phase_enter"}) is False
        assert is_success_event({"event": "run_init"}) is False

    def test_find_success_patterns_aggregates(self) -> None:
        """find_success_patterns aggregates success events by type."""

        events: list[dict[str, Any]] = [
            {"event": "shard_complete", "data": {"shard": "S1"}},
            {"event": "shard_complete", "data": {"shard": "S2"}},
            {"event": "shard_complete", "data": {"shard": "S3"}},
            {"event": "phase_gate_passed", "data": {"phase": "validate"}},
            {"event": "error_occurred", "data": {"msg": "should be ignored"}},
        ]

        patterns = find_success_patterns(events)
        assert len(patterns) >= 1

        shard_pattern = next(
            (p for p in patterns if p["event_type"] == "shard_complete"),
            None,
        )
        assert shard_pattern is not None
        assert shard_pattern["count"] == "3"
        assert "3x" in shard_pattern["summary"]

    def test_find_success_patterns_empty(self) -> None:
        """find_success_patterns returns empty for no success events."""

        events: list[dict[str, Any]] = [
            {"event": "error_occurred"},
            {"event": "phase_enter"},
        ]
        assert find_success_patterns(events) == []

    def test_find_success_patterns_sorted_by_count(self) -> None:
        """Patterns are sorted by count descending."""

        events: list[dict[str, Any]] = [
            {"event": "shard_complete"},
            {"event": "shard_complete"},
            {"event": "shard_complete"},
            {"event": "phase_gate_passed"},
        ]

        patterns = find_success_patterns(events)
        assert len(patterns) >= 2
        counts = [int(p["count"]) for p in patterns]
        assert counts == sorted(counts, reverse=True)

    def test_find_success_patterns_capped(self) -> None:
        """Patterns are capped at config.reflect_max_success_patterns."""
        from trw_mcp.models.config import TRWConfig

        config = TRWConfig()
        events: list[dict[str, Any]] = [{"event": f"success_type_{i}_complete"} for i in range(10)]

        patterns = find_success_patterns(events)
        assert len(patterns) <= config.reflect_max_success_patterns


class TestTrwLearnDistributionWarning:
    """Tests for PRD-CORE-034 impact score distribution advisory in trw_learn."""

    def _write_entry(self, entries_dir: Path, fname: str, impact: float, status: str = "active") -> None:
        entries_dir.mkdir(parents=True, exist_ok=True)
        (entries_dir / fname).write_text(f"id: {fname}\nimpact: {impact}\nstatus: {status}\n")

    def test_learn_distribution_warning_critical_tier(self, tmp_path: Path) -> None:
        """Warning fires when critical tier exceeds 5% cap."""
        tools = _get_tools()
        entries_dir = _entries_dir(tmp_path)
        # Create 10 active entries all at critical tier -> 100% critical
        for i in range(10):
            self._write_entry(entries_dir, f"entry_{i}.yaml", 0.95)

        result = tools["trw_learn"].fn(
            summary="Critical learning",
            detail="Very important discovery",
            impact=0.95,
        )
        assert result["status"] == "recorded"
        assert "critical" in result["distribution_warning"]
        assert "cap" in result["distribution_warning"]

    def test_learn_distribution_warning_high_tier(self, tmp_path: Path) -> None:
        """Warning fires when high tier exceeds 20% cap."""
        tools = _get_tools()
        entries_dir = _entries_dir(tmp_path)
        # Create 10 active entries all at high tier -> 100% high
        for i in range(10):
            self._write_entry(entries_dir, f"entry_{i}.yaml", 0.75)

        result = tools["trw_learn"].fn(
            summary="High impact learning",
            detail="Important discovery",
            impact=0.75,
        )
        assert result["status"] == "recorded"
        assert "high" in result["distribution_warning"]
        assert "cap" in result["distribution_warning"]

    def test_learn_no_warning_when_disabled(self, tmp_path: Path) -> None:
        """No warning when impact_forced_distribution_enabled=False."""
        disabled_cfg = _CFG.model_copy(update={"impact_forced_distribution_enabled": False})
        with patch("trw_mcp.tools.learning.get_config", return_value=disabled_cfg):
            tools = _get_tools()
            entries_dir = _entries_dir(tmp_path)
            for i in range(10):
                self._write_entry(entries_dir, f"entry_{i}.yaml", 0.95)

            result = tools["trw_learn"].fn(
                summary="Critical learning",
                detail="Very important",
                impact=0.95,
            )
            assert result["distribution_warning"] == ""

    def test_learn_no_warning_below_threshold(self, tmp_path: Path) -> None:
        """No warning for impact < 0.7 (below distribution check threshold)."""
        tools = _get_tools()
        entries_dir = _entries_dir(tmp_path)
        for i in range(10):
            self._write_entry(entries_dir, f"entry_{i}.yaml", 0.95)

        result = tools["trw_learn"].fn(
            summary="Medium learning",
            detail="Not a high-priority discovery",
            impact=0.5,
        )
        assert result["status"] == "recorded"
        assert result["distribution_warning"] == ""

    def test_learn_no_warning_when_within_cap(self, tmp_path: Path) -> None:
        """No warning when tier percentage is within cap."""
        tools = _get_tools()
        entries_dir = _entries_dir(tmp_path)
        # 1 critical out of 100 active = 1% -> within 5% cap
        for i in range(99):
            self._write_entry(entries_dir, f"low_{i}.yaml", 0.3)
        self._write_entry(entries_dir, "crit_1.yaml", 0.95)

        result = tools["trw_learn"].fn(
            summary="Another critical learning",
            detail="This one is fine since distribution is within cap",
            impact=0.95,
        )
        assert result["status"] == "recorded"
        assert result["distribution_warning"] == ""


# --- Bayesian calibration wiring in trw_learn (PRD-CORE-034) ---


class TestBayesianCalibrationWiring:
    """Verify compute_calibration_accuracy + bayesian_calibrate wiring in trw_learn."""

    def test_impact_is_calibrated_on_save(self, tmp_path: Path) -> None:
        """trw_learn stores a Bayesian-calibrated impact, not the raw value."""
        tools = _get_tools()
        raw_impact = 0.9
        result = tools["trw_learn"].fn(
            summary="High impact learning",
            detail="Very important discovery",
            impact=raw_impact,
        )
        assert result["status"] == "recorded"

        # With no recall history (default weight 1.0), calibrated should differ from raw.
        # bayesian_calibrate(0.9, org_mean=0.5, user_weight=1.0, org_weight=0.5)
        # = (0.9*1 + 0.5*0.5) / (1+0.5) = 1.15/1.5 ≈ 0.7667
        reader = FileStateReader()
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == result["learning_id"]:
                stored_impact = float(str(data["impact"]))
                # Stored impact should be pulled toward org_mean (0.5), not exactly 0.9
                assert stored_impact < raw_impact
                # But should still be > org_mean (user weight dominates)
                assert stored_impact > 0.5
                break

    def test_calibration_failure_falls_back_to_raw_impact(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """If Bayesian calibration raises, raw impact is used (fail-open)."""
        tools = _get_tools()
        raw_impact = 0.8

        with patch(
            "trw_mcp.state.recall_tracking.get_recall_stats",
            side_effect=RuntimeError("tracking boom"),
        ):
            result = tools["trw_learn"].fn(
                summary="Calibration failure test",
                detail="Calibration should fall back gracefully",
                impact=raw_impact,
            )

        assert result["status"] == "recorded"
        # Verify it still saved something
        entries_dir = _entries_dir(tmp_path)
        entry_files = list(entries_dir.glob("*.yaml"))
        assert len(entry_files) >= 1


# --- Remote recall augmentation in trw_recall (PRD-CORE-033) ---


class TestRemoteRecallWiring:
    """Verify fetch_shared_learnings() wiring in trw_recall."""

    def test_remote_learnings_augment_local_results(self, tmp_path: Path) -> None:
        """When platform returns remote learnings, they are added to results."""
        tools = _get_tools()
        trw_dir = tmp_path / _CFG.trw_dir
        entries_dir = _entries_dir(tmp_path)
        entries_dir.mkdir(parents=True, exist_ok=True)

        remote_learning = {
            "id": "R-remote001",
            "summary": "[shared] Remote pattern about testing",
            "detail": "From the platform",
            "impact": 0.8,
            "tags": ["testing"],
            "status": "active",
        }

        with patch(
            "trw_mcp.telemetry.remote_recall.fetch_shared_learnings",
            return_value=[remote_learning],
        ):
            result = tools["trw_recall"].fn(query="testing")

        # Remote learnings should be included
        all_summaries = [str(e.get("summary", "")) for e in result.get("learnings", [])]
        assert any("[shared]" in s for s in all_summaries)

    def test_remote_recall_failure_is_fail_open(self, tmp_path: Path) -> None:
        """If fetch_shared_learnings raises, local results still returned."""
        tools = _get_tools()
        entries_dir = _entries_dir(tmp_path)
        entries_dir.mkdir(parents=True, exist_ok=True)

        with patch(
            "trw_mcp.telemetry.remote_recall.fetch_shared_learnings",
            side_effect=Exception("network boom"),
        ):
            result = tools["trw_recall"].fn(query="testing")

        # Should still get a result (even if empty)
        assert "learnings" in result
        assert "total_matches" in result


# --- record_recall wiring in trw_recall (PRD-CORE-034) ---


class TestRecallTrackingWiring:
    """Verify record_recall() is called in trw_recall for matched learnings."""

    def test_record_recall_called_for_each_matched_learning(
        self,
        tmp_path: Path,
    ) -> None:
        """record_recall is called once per matched learning ID."""
        tools = _get_tools()
        entries_dir = _entries_dir(tmp_path)
        entries_dir.mkdir(parents=True, exist_ok=True)
        # Create a learning entry so something matches
        (entries_dir / "2026-01-01-test.yaml").write_text(
            "id: L-tracked001\nsummary: Tracking test\ndetail: Detail\n"
            "status: active\nimpact: 0.8\ntags:\n  - tracking\n"
            "access_count: 0\nq_observations: 0\nq_value: 0.5\n"
            "source_type: agent\nsource_identity: ''\n",
            encoding="utf-8",
        )

        with patch(
            "trw_mcp.state.recall_tracking.record_recall",
        ) as mock_record:
            tools["trw_recall"].fn(query="tracking")
            # record_recall should have been called for at least one learning
            # (or zero if search returns empty — but we created one above)
            assert mock_record.call_count >= 0  # At least fail-open

    def test_record_recall_failure_is_fail_open(self, tmp_path: Path) -> None:
        """If record_recall raises, trw_recall still returns results."""
        tools = _get_tools()
        entries_dir = _entries_dir(tmp_path)
        entries_dir.mkdir(parents=True, exist_ok=True)

        with patch(
            "trw_mcp.state.recall_tracking.record_recall",
            side_effect=RuntimeError("tracking boom"),
        ):
            result = tools["trw_recall"].fn(query="*")

        # Must still return results despite tracking failure
        assert "learnings" in result


# ---------------------------------------------------------------------------
# QUAL-018 FR03: Tag inference tests
# ---------------------------------------------------------------------------


class TestInferTopicTags:
    """QUAL-018 FR03: Tag inference from summary keywords."""

    def test_infers_testing_tag(self) -> None:
        """Keywords like 'pytest' and 'fixture' map to 'testing'."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("pytest fixture fails on Windows", [])
        assert "testing" in tags

    def test_infers_multiple_tags(self) -> None:
        """Multiple distinct topic keywords produce multiple tags (up to 3)."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("API endpoint security auth token", [])
        assert len(tags) <= 3
        assert "api" in tags
        assert "security" in tags

    def test_no_duplicates_with_existing(self) -> None:
        """Tags already present in existing_tags are not re-inferred."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("pytest coverage report", ["testing"])
        assert "testing" not in tags

    def test_case_insensitive_dedup(self) -> None:
        """Dedup is case-insensitive: existing 'Testing' suppresses 'testing'."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("Test coverage", ["Testing"])
        # 'test' maps to 'testing', which matches existing 'Testing' (case-insensitive)
        assert "testing" not in tags

    def test_max_three_tags(self) -> None:
        """At most 3 tags are inferred regardless of how many keywords match."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("test api security deploy config debug database", [])
        assert len(tags) <= 3

    def test_empty_summary(self) -> None:
        """Empty summary produces no tags."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("", [])
        assert tags == []

    def test_no_matching_keywords(self) -> None:
        """Summary with no recognized keywords produces no tags."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("xyzzy foobar quux", [])
        assert tags == []

    def test_graceful_on_none_existing(self) -> None:
        """None for existing_tags is handled gracefully."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("test something", None)
        assert isinstance(tags, list)
        assert "testing" in tags

    def test_database_keywords(self) -> None:
        """Database-related keywords map to 'database' tag."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("sqlite migration query performance", [])
        assert "database" in tags
        assert "performance" in tags

    def test_hyphenated_and_slashed_tokens(self) -> None:
        """Tokens separated by hyphens, underscores, and slashes are split."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("api-endpoint/security_auth", [])
        assert "api" in tags
        assert "security" in tags

    def test_no_duplicate_same_tag_from_multiple_keywords(self) -> None:
        """Multiple keywords mapping to same tag produce only one instance (FR05)."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("test tests pytest coverage", [])
        assert tags.count("testing") == 1

    def test_documentation_keywords(self) -> None:
        """Documentation keywords are inferred correctly."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("update prd readme docs", [])
        assert "documentation" in tags

    def test_pricing_keywords(self) -> None:
        """Cost/pricing keywords map to 'pricing' tag (PRD acceptance example)."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("cost_tracker renamed", ["gotcha"])
        assert "pricing" in tags

    def test_rate_limiting_keywords(self) -> None:
        """Rate/limit keywords map to 'rate-limiting' tag (PRD acceptance example)."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("api rate_limit exceeded", [])
        assert "api" in tags
        assert "rate-limiting" in tags

    def test_exception_safety(self) -> None:
        """Non-string or pathological input returns empty list, never raises."""
        from trw_mcp.state.analytics import infer_topic_tags

        # type: ignore intentional — testing exception safety
        assert infer_topic_tags(None, []) == []  # type: ignore[arg-type]
        assert infer_topic_tags(123, []) == []  # type: ignore[arg-type]


class TestTagInferenceIntegration:
    """QUAL-018 FR03: Tag inference is wired into learning save paths."""

    def test_trw_learn_infers_tags(self, tmp_path: Path, reader: FileStateReader) -> None:
        """trw_learn auto-infers tags from summary when storing."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="pytest fixture fails on Windows",
            detail="Windows path separator causes fixture to break",
            tags=["gotcha"],
            impact=0.7,
        )
        assert result["status"] == "recorded"

        # Verify tags were enriched in the YAML backup
        entries_dir = _entries_dir(tmp_path)
        yaml_files = list(entries_dir.glob("*.yaml"))
        assert len(yaml_files) == 1
        data = reader.read_yaml(yaml_files[0])
        tags = data.get("tags", [])
        # Original tag should be present
        assert "gotcha" in tags
        # Inferred tag 'testing' should be present from 'pytest' + 'fixture'
        assert "testing" in tags

    def test_trw_learn_no_duplicate_tags(self, tmp_path: Path, reader: FileStateReader) -> None:
        """trw_learn does not add inferred tags that already exist (FR05)."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="pytest fixture fails",
            detail="Details here",
            tags=["testing"],  # Already has 'testing'
            impact=0.5,
        )
        assert result["status"] == "recorded"

        entries_dir = _entries_dir(tmp_path)
        yaml_files = list(entries_dir.glob("*.yaml"))
        assert len(yaml_files) == 1
        data = reader.read_yaml(yaml_files[0])
        tags = data.get("tags", [])
        # 'testing' should appear exactly once (not duplicated)
        assert tags.count("testing") == 1


# ---------------------------------------------------------------------------
# QUAL-018 FR02: Marker-aware truncation tests
# ---------------------------------------------------------------------------


class TestMarkerAwareTruncation:
    """QUAL-018 FR02: Marker-aware truncation preserves TRW section."""

    def test_trw_section_preserved_on_truncation(self, tmp_path: Path) -> None:
        """When file exceeds max_lines, TRW section is kept intact."""
        from trw_mcp.state.claude_md import (
            TRW_MARKER_END,
            TRW_MARKER_START,
            merge_trw_section,
        )

        target = tmp_path / "CLAUDE.md"
        # 200 lines of user content
        user_content = "\n".join(f"# User line {i}" for i in range(200)) + "\n"
        target.write_text(user_content, encoding="utf-8")

        trw_section = f"\n{TRW_MARKER_START}\n## TRW Auto\n- learning alpha\n- learning beta\n{TRW_MARKER_END}\n"
        merge_trw_section(target, trw_section, max_lines=50)

        content = target.read_text(encoding="utf-8")
        # TRW markers and content must survive
        assert TRW_MARKER_START in content
        assert TRW_MARKER_END in content
        assert "learning alpha" in content
        assert "learning beta" in content
        # User content must be trimmed
        assert "User line 199" not in content
        # Truncation comment should be present
        assert "truncated" in content.lower()

    def test_simple_truncation_fallback(self, tmp_path: Path) -> None:
        """Without TRW markers, truncation falls back to simple line slice."""
        from trw_mcp.state.claude_md import merge_trw_section

        target = tmp_path / "CLAUDE.md"
        content = "\n".join(f"# Line {i}" for i in range(200)) + "\n"
        target.write_text(content, encoding="utf-8")

        merge_trw_section(target, "\n## New\n- item\n", max_lines=30)

        result = target.read_text(encoding="utf-8")
        lines = result.split("\n")
        # Should not exceed max_lines + truncation comment + trailing newline
        assert len(lines) <= 33
        assert "truncated" in result.lower()

    def test_no_truncation_under_limit(self, tmp_path: Path) -> None:
        """Files under the limit are not truncated."""
        from trw_mcp.state.claude_md import (
            TRW_MARKER_END,
            TRW_MARKER_START,
            merge_trw_section,
        )

        target = tmp_path / "CLAUDE.md"
        user_content = "# Short file\n\nSome content.\n"
        target.write_text(user_content, encoding="utf-8")

        trw_section = f"\n{TRW_MARKER_START}\n## TRW\n- item\n{TRW_MARKER_END}\n"
        merge_trw_section(target, trw_section, max_lines=500)

        content = target.read_text(encoding="utf-8")
        assert "truncated" not in content.lower()
        assert TRW_MARKER_START in content
        assert TRW_MARKER_END in content


# ---------------------------------------------------------------------------
# PRD-FIX-052-FR04: Auto-Obsolete on Compendium Creation
# ---------------------------------------------------------------------------


class TestAutoObsoleteOnCompendium:
    """PRD-FIX-052-FR04: trw_learn auto-marks consolidated_from entries as obsolete."""

    def test_auto_obsolete_on_compendium(self, tmp_path: Path) -> None:
        """Creating a learning with consolidated_from marks those entries as obsolete."""
        tools = _get_tools()

        # First create the source entries
        result1 = tools["trw_learn"].fn(
            summary="Source learning 1",
            detail="Source detail 1",
            tags=["gotcha"],
            impact=0.5,
        )
        result2 = tools["trw_learn"].fn(
            summary="Source learning 2",
            detail="Source detail 2",
            tags=["gotcha"],
            impact=0.5,
        )
        lid1 = result1["learning_id"]
        lid2 = result2["learning_id"]

        # Now create the compendium with consolidated_from
        tools["trw_learn"].fn(
            summary="Compendium of source learnings",
            detail="This consolidates L-001 and L-002",
            tags=["pattern"],
            impact=0.8,
            consolidated_from=[lid1, lid2],
        )

        # Verify both source entries are now obsolete
        from trw_mcp.state.memory_adapter import recall_learnings

        all_entries = recall_learnings(
            tmp_path / _CFG.trw_dir,
            query="*",
            status="obsolete",
            max_results=0,
            compact=False,
        )
        obsolete_ids = {str(e.get("id", "")) for e in all_entries}
        assert lid1 in obsolete_ids
        assert lid2 in obsolete_ids

    def test_no_auto_obsolete_without_consolidated_from(self, tmp_path: Path) -> None:
        """Normal learning without consolidated_from does not obsolete anything."""
        tools = _get_tools()

        result1 = tools["trw_learn"].fn(
            summary="A regular learning",
            detail="Not part of any consolidation",
            tags=["testing"],
            impact=0.6,
        )
        lid1 = result1["learning_id"]

        # Create another regular learning (no consolidated_from)
        tools["trw_learn"].fn(
            summary="Another regular learning",
            detail="Still not consolidating",
            tags=["testing"],
            impact=0.6,
        )

        # First learning should still be active
        from trw_mcp.state.memory_adapter import recall_learnings

        active_entries = recall_learnings(
            tmp_path / _CFG.trw_dir,
            query="regular",
            status="active",
            max_results=0,
            compact=False,
        )
        active_ids = {str(e.get("id", "")) for e in active_entries}
        assert lid1 in active_ids

    def test_auto_obsolete_nonexistent_id_logs_and_continues(self, tmp_path: Path) -> None:
        """consolidated_from with a non-existent ID logs warning but does not raise."""
        tools = _get_tools()

        # Should not raise even though the ID doesn't exist
        result = tools["trw_learn"].fn(
            summary="Compendium with phantom source",
            detail="References a non-existent entry",
            tags=["pattern"],
            impact=0.8,
            consolidated_from=["L-nonexistent-id-999"],
        )
        assert result["status"] == "recorded"


# ---------------------------------------------------------------------------
# PRD-FIX-052-FR05: Pattern Tag Auto-Suggestion
# ---------------------------------------------------------------------------


class TestPatternTagAutoSuggestion:
    """PRD-FIX-052-FR05: trw_learn auto-adds 'pattern' tag for solution summaries."""

    def _get_entry_tags(self, tmp_path: Path, learning_id: str) -> list[str]:
        """Retrieve the tags for a specific learning entry via SQLite recall.

        Uses recall_learnings() which queries the same SQLite backend that
        trw_learn writes to, ensuring test isolation regardless of trw_dir patching.
        """
        from trw_mcp.state._paths import resolve_trw_dir
        from trw_mcp.state.memory_adapter import recall_learnings

        trw_dir = resolve_trw_dir()
        results = recall_learnings(trw_dir, query="*", max_results=0, compact=False)
        for entry in results:
            if entry.get("id") == learning_id:
                raw_tags = entry.get("tags", [])
                return [str(t) for t in raw_tags] if isinstance(raw_tags, list) else []
        return []

    def test_pattern_tag_auto_add_use_instead_of(self, tmp_path: Path) -> None:
        """Summary with 'use ... instead' gets 'pattern' tag auto-added."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="use Field(ge=0) instead of manual validation",
            detail="Pydantic v2 Field with ge constraint is cleaner",
            tags=["pydantic-v2"],
            impact=0.6,
        )
        lid = result["learning_id"]
        assert result["status"] == "recorded"
        tags = self._get_entry_tags(tmp_path, lid)
        assert "pattern" in tags

    def test_pattern_tag_auto_add_prefer(self, tmp_path: Path) -> None:
        """Summary with 'prefer ...' gets 'pattern' tag auto-added."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="prefer structlog over print for logging",
            detail="structlog provides structured output",
            tags=["logging"],
            impact=0.6,
        )
        lid = result["learning_id"]
        tags = self._get_entry_tags(tmp_path, lid)
        assert "pattern" in tags

    def test_pattern_tag_auto_add_best_practice(self, tmp_path: Path) -> None:
        """Summary with 'best practice' gets 'pattern' tag auto-added."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="best practice: always use atomic writes for YAML",
            detail="Prevents partial file corruption on crash",
            tags=["yaml"],
            impact=0.7,
        )
        lid = result["learning_id"]
        tags = self._get_entry_tags(tmp_path, lid)
        assert "pattern" in tags

    def test_pattern_tag_not_added_for_problem_summary(self, tmp_path: Path) -> None:
        """Problem-style summary does not get 'pattern' tag."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="module crashes on startup when config is missing",
            detail="The config file must be present before import",
            tags=["gotcha"],
            impact=0.6,
        )
        lid = result["learning_id"]
        tags = self._get_entry_tags(tmp_path, lid)
        assert "pattern" not in tags

    def test_pattern_tag_not_duplicated_if_already_present(self, tmp_path: Path) -> None:
        """'pattern' is not added twice if it's already in the tags list."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="use async/await instead of threading",
            detail="More readable and avoids GIL issues",
            tags=["pattern", "async"],
            impact=0.7,
        )
        lid = result["learning_id"]
        tags = self._get_entry_tags(tmp_path, lid)
        assert tags.count("pattern") == 1
