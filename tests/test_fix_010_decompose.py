"""Tests for PRD-FIX-010: learning.py decomposition.

Verifies that:
1. learning.py delegates to new/enhanced state modules
2. New modules are importable and functional
3. All 7 tool functions still work (regression)
4. Module line counts meet targets
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


_CFG = TRWConfig()


# ---------------------------------------------------------------------------
# Test: module structure and imports from new modules
# ---------------------------------------------------------------------------

class TestModuleImports:
    """Verify new/enhanced modules are importable with correct APIs."""

    def test_imports_from_llm_helpers(self) -> None:
        """LLM helper functions are importable from state.llm_helpers."""
        from trw_mcp.state.llm_helpers import (
            LLM_BATCH_CAP,
            LLM_EVENT_CAP,
            llm_assess_learnings,
            llm_extract_learnings,
            llm_summarize_learnings,
        )
        assert LLM_BATCH_CAP == 20
        assert LLM_EVENT_CAP == 30
        assert callable(llm_assess_learnings)
        assert callable(llm_extract_learnings)
        assert callable(llm_summarize_learnings)

    def test_imports_from_recall_search(self) -> None:
        """Recall search functions are importable from state.recall_search."""
        from trw_mcp.state.recall_search import (
            collect_context,
            search_entries,
            search_patterns,
            update_access_tracking,
        )
        assert callable(search_entries)
        assert callable(search_patterns)
        assert callable(update_access_tracking)
        assert callable(collect_context)

    def test_imports_from_analytics_new_functions(self) -> None:
        """New analytics extraction functions are importable."""
        from trw_mcp.state.analytics import (
            extract_learnings_from_llm,
            extract_learnings_mechanical,
            find_success_patterns,
            is_success_event,
        )
        assert callable(extract_learnings_from_llm)
        assert callable(extract_learnings_mechanical)
        assert callable(find_success_patterns)
        assert callable(is_success_event)

    def test_imports_from_claude_md_new_functions(self) -> None:
        """New claude_md collection functions are importable."""
        from trw_mcp.state.claude_md import (
            collect_context_data,
            collect_patterns,
            collect_promotable_learnings,
        )
        assert callable(collect_promotable_learnings)
        assert callable(collect_patterns)
        assert callable(collect_context_data)


# ---------------------------------------------------------------------------
# Test: recall_search module
# ---------------------------------------------------------------------------

class TestRecallSearch:
    """Unit tests for state.recall_search functions."""

    def test_search_entries_finds_matching(self, tmp_project: Path) -> None:
        """search_entries returns entries matching query tokens."""
        writer = FileStateWriter()
        entries_dir = tmp_project / ".trw" / "learnings" / "entries"
        writer.write_yaml(entries_dir / "test.yaml", {
            "id": "L-test001",
            "summary": "JWT authentication gotcha",
            "detail": "Tokens expire silently",
            "tags": ["auth", "gotcha"],
            "impact": 0.8,
            "status": "active",
        })
        reader = FileStateReader()
        from trw_mcp.state.recall_search import search_entries
        matches, files = search_entries(entries_dir, ["jwt"], reader)
        assert len(matches) == 1
        assert matches[0]["id"] == "L-test001"
        assert len(files) == 1

    def test_search_entries_respects_min_impact(self, tmp_project: Path) -> None:
        """search_entries filters by min_impact."""
        writer = FileStateWriter()
        entries_dir = tmp_project / ".trw" / "learnings" / "entries"
        writer.write_yaml(entries_dir / "low.yaml", {
            "id": "L-low", "summary": "low impact",
            "detail": "", "tags": [], "impact": 0.2, "status": "active",
        })
        writer.write_yaml(entries_dir / "high.yaml", {
            "id": "L-high", "summary": "high impact",
            "detail": "", "tags": [], "impact": 0.9, "status": "active",
        })
        reader = FileStateReader()
        from trw_mcp.state.recall_search import search_entries
        matches, _ = search_entries(entries_dir, [], reader, min_impact=0.5)
        assert len(matches) == 1
        assert matches[0]["id"] == "L-high"

    def test_search_patterns_finds_matching(self, tmp_project: Path) -> None:
        """search_patterns returns patterns matching query."""
        writer = FileStateWriter()
        patterns_dir = tmp_project / ".trw" / "patterns"
        writer.write_yaml(patterns_dir / "p1.yaml", {
            "name": "research-map-reduce",
            "description": "3-wave research pattern",
        })
        reader = FileStateReader()
        from trw_mcp.state.recall_search import search_patterns
        matches = search_patterns(patterns_dir, ["research"], reader)
        assert len(matches) == 1

    def test_update_access_tracking_increments(self, tmp_project: Path) -> None:
        """update_access_tracking increments access_count."""
        writer = FileStateWriter()
        reader = FileStateReader()
        entries_dir = tmp_project / ".trw" / "learnings" / "entries"
        entry_path = entries_dir / "track.yaml"
        writer.write_yaml(entry_path, {
            "id": "L-track", "summary": "test",
            "access_count": 0, "last_accessed_at": None,
        })
        from trw_mcp.state.recall_search import update_access_tracking
        ids = update_access_tracking([entry_path], reader, writer)
        assert ids == ["L-track"]
        updated = reader.read_yaml(entry_path)
        assert updated["access_count"] == 1


# ---------------------------------------------------------------------------
# Test: analytics extraction functions
# ---------------------------------------------------------------------------

class TestAnalyticsExtraction:
    """Unit tests for mechanical learning extraction."""

    def test_extract_learnings_mechanical_errors(self, tmp_project: Path) -> None:
        """extract_learnings_mechanical creates entries from error events."""
        from trw_mcp.state.analytics import extract_learnings_mechanical
        trw_dir = tmp_project / ".trw"
        errors = [{"event": "tool_error", "data": "disk full", "ts": "2026-01-01"}]
        result = extract_learnings_mechanical(errors, [], trw_dir)
        assert len(result) == 1
        assert "Error pattern" in result[0]["summary"]

    def test_extract_learnings_mechanical_repeated(self, tmp_project: Path) -> None:
        """extract_learnings_mechanical creates entries from repeated ops."""
        from trw_mcp.state.analytics import extract_learnings_mechanical
        trw_dir = tmp_project / ".trw"
        ops = [("git_push", 5)]
        result = extract_learnings_mechanical([], ops, trw_dir)
        assert len(result) == 1
        assert "Repeated operation" in result[0]["summary"]

    def test_extract_learnings_from_llm_saves_entries(self, tmp_project: Path) -> None:
        """extract_learnings_from_llm persists entries to disk."""
        from trw_mcp.state.analytics import extract_learnings_from_llm
        trw_dir = tmp_project / ".trw"
        items: list[dict[str, object]] = [
            {"summary": "LLM insight", "detail": "details", "tags": ["llm"], "impact": "0.7"},
        ]
        result = extract_learnings_from_llm(items, trw_dir)
        assert len(result) == 1
        assert result[0]["summary"] == "LLM insight"
        # Verify file was written
        entries_dir = trw_dir / "learnings" / "entries"
        assert len(list(entries_dir.glob("*.yaml"))) >= 1


# ---------------------------------------------------------------------------
# Test: claude_md collection functions
# ---------------------------------------------------------------------------

class TestClaudeMdCollection:
    """Unit tests for claude_md collection helpers."""

    def test_collect_promotable_learnings(self, tmp_project: Path) -> None:
        """collect_promotable_learnings returns high-impact active entries."""
        from trw_mcp.state.claude_md import collect_promotable_learnings
        writer = FileStateWriter()
        reader = FileStateReader()
        config = TRWConfig()
        entries_dir = tmp_project / ".trw" / "learnings" / "entries"
        writer.write_yaml(entries_dir / "high.yaml", {
            "id": "L-high", "summary": "important",
            "status": "active", "impact": 0.9,
            "q_observations": 0, "q_value": 0.5,
        })
        writer.write_yaml(entries_dir / "low.yaml", {
            "id": "L-low", "summary": "trivial",
            "status": "active", "impact": 0.2,
            "q_observations": 0, "q_value": 0.1,
        })
        result = collect_promotable_learnings(tmp_project / ".trw", config, reader)
        assert any(d["id"] == "L-high" for d in result)
        assert not any(d["id"] == "L-low" for d in result)

    def test_collect_patterns(self, tmp_project: Path) -> None:
        """collect_patterns returns non-index pattern files."""
        from trw_mcp.state.claude_md import collect_patterns
        writer = FileStateWriter()
        reader = FileStateReader()
        config = TRWConfig()
        patterns_dir = tmp_project / ".trw" / "patterns"
        writer.write_yaml(patterns_dir / "p1.yaml", {"name": "test-pattern"})
        writer.write_yaml(patterns_dir / "index.yaml", {"patterns": []})
        result = collect_patterns(tmp_project / ".trw", config, reader)
        assert len(result) == 1
        assert result[0]["name"] == "test-pattern"

    def test_collect_context_data(self, tmp_project: Path) -> None:
        """collect_context_data returns arch and conv data."""
        from trw_mcp.state.claude_md import collect_context_data
        writer = FileStateWriter()
        reader = FileStateReader()
        config = TRWConfig()
        context_dir = tmp_project / ".trw" / "context"
        writer.write_yaml(context_dir / "architecture.yaml", {"style": "hexagonal"})
        writer.write_yaml(context_dir / "conventions.yaml", {"naming": "snake_case"})
        arch, conv = collect_context_data(tmp_project / ".trw", config, reader)
        assert arch["style"] == "hexagonal"
        assert conv["naming"] == "snake_case"


# ---------------------------------------------------------------------------
# Test: learning.py line count target
# ---------------------------------------------------------------------------

class TestDecompositionTargets:
    """Verify decomposition metrics meet PRD-FIX-010 targets."""

    def test_learning_py_under_600_lines(self) -> None:
        """learning.py should be under 600 lines after decomposition."""
        learning_path = Path(__file__).parent.parent / "src" / "trw_mcp" / "tools" / "learning.py"
        line_count = len(learning_path.read_text(encoding="utf-8").splitlines())
        assert line_count < 600, f"learning.py is {line_count} lines, target < 600"

    def test_llm_helpers_under_400_lines(self) -> None:
        """state/llm_helpers.py should be under 400 lines."""
        path = Path(__file__).parent.parent / "src" / "trw_mcp" / "state" / "llm_helpers.py"
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        assert line_count < 400, f"llm_helpers.py is {line_count} lines, target < 400"

    def test_recall_search_under_400_lines(self) -> None:
        """state/recall_search.py should be under 400 lines."""
        path = Path(__file__).parent.parent / "src" / "trw_mcp" / "state" / "recall_search.py"
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        assert line_count < 400, f"recall_search.py is {line_count} lines, target < 400"


# ---------------------------------------------------------------------------
# Test: tool delegation regression
# ---------------------------------------------------------------------------

class TestToolDelegationIntact:
    """Verify all 7 tool functions remain registered and callable."""

    def test_all_seven_tools_registered(self) -> None:
        """All 7 learning tools should be registered on a test server."""
        from fastmcp import FastMCP
        from trw_mcp.tools.learning import register_learning_tools
        srv = FastMCP("test-fix-010")
        register_learning_tools(srv)
        tool_names = {t.name for t in srv._tool_manager._tools.values()}
        expected = {
            "trw_reflect", "trw_learn", "trw_learn_update",
            "trw_recall", "trw_script_save", "trw_claude_md_sync",
            "trw_learn_prune",
        }
        assert expected.issubset(tool_names), f"Missing tools: {expected - tool_names}"
