"""Behavioral equivalence tests for learning tools.

Verifies that learn, recall, and claude_md_sync produce correct output
structures with expected field presence, types, and key values.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests.conftest import get_tools_sync
from trw_mcp.models.config import TRWConfig

_CFG = TRWConfig()


@pytest.fixture(autouse=True)
def _set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("TRW_DEDUP_ENABLED", "false")
    return tmp_path


def _get_tools() -> dict[str, Any]:
    """Create fresh server and return tool map."""
    from fastmcp import FastMCP

    from trw_mcp.tools.learning import register_learning_tools

    srv = FastMCP("test")
    register_learning_tools(srv)
    return get_tools_sync(srv)


def _entries_dir(root: Path) -> Path:
    return root / _CFG.trw_dir / _CFG.learnings_dir / _CFG.entries_dir


def _seed_learning(tools: dict[str, Any], **kwargs: object) -> dict[str, str]:
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
        assert len(result["learning_id"]) == 6  # L- + 4 base62 chars (PRD-CORE-110)

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
