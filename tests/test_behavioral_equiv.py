"""Behavioral equivalence tests for FIX-010 decomposition.

Verifies that all 7 learning tools produce correct output structures
with expected field presence, types, and key values after the learning.py
decomposition into specialized state modules.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.models.config import TRWConfig

_CFG = TRWConfig()


@pytest.fixture(autouse=True)
def _set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    import trw_mcp.tools.learning as learn_mod

    monkeypatch.setattr(learn_mod, "_config", learn_mod.TRWConfig())
    return tmp_path


def _get_tools() -> dict[str, object]:
    """Create fresh server and return tool map."""
    from fastmcp import FastMCP
    from trw_mcp.tools.learning import register_learning_tools

    srv = FastMCP("test")
    register_learning_tools(srv)
    return {t.name: t for t in srv._tool_manager._tools.values()}


def _entries_dir(root: Path) -> Path:
    return root / _CFG.trw_dir / _CFG.learnings_dir / _CFG.entries_dir


def _seed_learning(tools: dict[str, object], **kwargs: object) -> dict[str, str]:
    """Record a learning and return the result."""
    defaults = {
        "summary": "Test learning entry",
        "detail": "Detailed description for testing",
        "tags": ["testing"],
        "impact": 0.8,
    }
    defaults.update(kwargs)
    return tools["trw_learn"].fn(**defaults)


# --- trw_learn behavioral equivalence ---


@pytest.mark.unit
class TestLearnBehavior:
    """trw_learn output structure matches expected contract."""

    def test_learn_returns_required_fields(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = _seed_learning(tools)
        assert "learning_id" in result
        assert "status" in result
        assert result["status"] == "recorded"

    def test_learn_id_format(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = _seed_learning(tools)
        assert result["learning_id"].startswith("L-")
        assert len(result["learning_id"]) == 10  # L- + 8 hex chars

    def test_learn_creates_yaml_file(self, tmp_path: Path) -> None:
        tools = _get_tools()
        _seed_learning(tools)
        entries = list(_entries_dir(tmp_path).glob("*.yaml"))
        assert len(entries) == 1


# --- trw_recall behavioral equivalence ---


@pytest.mark.unit
class TestRecallBehavior:
    """trw_recall output structure matches expected contract."""

    def test_recall_returns_required_fields(self, tmp_path: Path) -> None:
        tools = _get_tools()
        _seed_learning(tools, summary="Database pooling gotcha")
        result = tools["trw_recall"].fn(query="database")
        assert "learnings" in result
        assert "total_matches" in result
        assert "total_available" in result
        assert isinstance(result["learnings"], list)

    def test_recall_compact_mode_fields(self, tmp_path: Path) -> None:
        tools = _get_tools()
        _seed_learning(tools, summary="Compact mode test")
        result = tools["trw_recall"].fn(query="*", compact=True)
        assert "compact" in result
        assert result["compact"] is True
        if result["learnings"]:
            entry = result["learnings"][0]
            # Compact mode should only have a subset of fields
            assert "id" in entry
            assert "summary" in entry

    def test_recall_empty_result_structure(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_recall"].fn(query="nonexistent-xyz-query")
        assert result["total_matches"] == 0
        assert result["learnings"] == []


# --- trw_reflect behavioral equivalence ---


@pytest.mark.integration
class TestReflectBehavior:
    """trw_reflect output structure matches expected contract."""

    def test_reflect_returns_required_fields(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_reflect"].fn()
        # reflect returns reflection_id, scope, events_analyzed, etc.
        assert "reflection_id" in result
        assert "events_analyzed" in result
        assert "scope" in result
        assert isinstance(result["events_analyzed"], int)

    def test_reflect_with_scope(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_reflect"].fn(scope="session")
        assert result["scope"] == "session"
        assert "new_learnings" in result
        assert isinstance(result["new_learnings"], list)

    def test_reflect_returns_quality_metrics(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_reflect"].fn()
        assert "reflection_quality" in result
        rq = result["reflection_quality"]
        assert "score" in rq
        assert "components" in rq


# --- trw_learn_update behavioral equivalence ---


@pytest.mark.integration
class TestLearnUpdateBehavior:
    """trw_learn_update output structure matches expected contract."""

    def test_update_returns_required_fields(self, tmp_path: Path) -> None:
        tools = _get_tools()
        learn_result = _seed_learning(tools)
        lid = learn_result["learning_id"]

        result = tools["trw_learn_update"].fn(
            learning_id=lid, impact=0.9,
        )
        assert "learning_id" in result
        assert "status" in result
        assert result["status"] == "updated"
        assert result["learning_id"] == lid

    def test_update_not_found_returns_error(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_learn_update"].fn(
            learning_id="L-nonexist", summary="new summary",
        )
        assert "error" in result
        assert result["learning_id"] == "L-nonexist"


# --- trw_learn_prune behavioral equivalence ---


@pytest.mark.integration
class TestLearnPruneBehavior:
    """trw_learn_prune output structure matches expected contract."""

    def test_prune_dry_run_returns_required_fields(self, tmp_path: Path) -> None:
        tools = _get_tools()
        _seed_learning(tools)
        result = tools["trw_learn_prune"].fn(dry_run=True)
        # Prune returns candidates, actions, dry_run, method
        assert "candidates" in result
        assert "actions" in result
        assert "dry_run" in result
        assert result["dry_run"] is True

    def test_prune_dry_run_does_not_modify(self, tmp_path: Path) -> None:
        tools = _get_tools()
        _seed_learning(tools)
        before = list(_entries_dir(tmp_path).glob("*.yaml"))
        tools["trw_learn_prune"].fn(dry_run=True)
        after = list(_entries_dir(tmp_path).glob("*.yaml"))
        assert len(before) == len(after)


# --- trw_script_save behavioral equivalence ---


@pytest.mark.integration
class TestScriptSaveBehavior:
    """trw_script_save output structure matches expected contract."""

    def test_script_save_returns_required_fields(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_script_save"].fn(
            name="test-script",
            content="#!/bin/bash\necho hello",
            description="A test script",
            language="bash",
        )
        assert "status" in result
        assert result["status"] == "created"
        assert "path" in result
        assert "name" in result

    def test_script_save_creates_file(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_script_save"].fn(
            name="my-script",
            content="print('hello')",
            description="Python test",
            language="python",
        )
        saved_path = Path(result["path"])
        assert saved_path.exists()


# --- trw_claude_md_sync behavioral equivalence ---


@pytest.mark.integration
class TestClaudeMdSyncBehavior:
    """trw_claude_md_sync output structure matches expected contract."""

    def test_sync_returns_required_fields(self, tmp_path: Path) -> None:
        tools = _get_tools()
        _seed_learning(tools, impact=0.9)

        with patch(
            "trw_mcp.state.claude_md.resolve_project_root",
            return_value=tmp_path,
        ):
            result = tools["trw_claude_md_sync"].fn()

        assert "status" in result
        assert result["status"] in ("success", "synced")
        assert "learnings_promoted" in result
        assert isinstance(result["learnings_promoted"], int)
