"""Behavioral equivalence tests for learning tools.

Verifies that learn, recall, and claude_md_sync produce correct output
structures with expected field presence, types, and key values.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests._memory_fixtures import MemoryDaemon, attach_checkout
from tests._tools_learning_shared import instructions_sync_fn
from tests.conftest import get_tools_sync
from trw_mcp.models.config import TRWConfig

_CFG = TRWConfig()


@pytest.fixture(autouse=True)
def _set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory_daemon: MemoryDaemon) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("TRW_DEDUP_ENABLED", "false")
    # PRD-CORE-280 slice e1: this workspace is built directly (not via
    # ``daemon_checkout``), so pin it to the shared session daemon per the
    # fixture contract's "test that builds its own .trw" note.
    trw_dir = tmp_path / str(_CFG.trw_dir)
    (trw_dir / _CFG.learnings_dir / _CFG.entries_dir).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))
    attach_checkout(trw_dir, memory_daemon)
    from trw_mcp.models.config import reload_config

    reload_config()
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
        assert isinstance(result["learnings"], list)
        # PRD-CORE-294 FR01: default rows are stubs {id, claim, anchor?}.
        entry = result["learnings"][0]
        assert "id" in entry
        assert "claim" in entry
        assert "summary" not in entry

    def test_recall_empty_result_structure(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_recall"].fn(query="nonexistent-xyz-query")
        assert result["total_matches"] == 0
        assert result["learnings"] == []


# --- instructions sync behavioral equivalence ---


@pytest.mark.integration
class TestClaudeMdSyncBehavior:
    """instructions sync output structure matches expected contract."""

    def test_sync_returns_required_fields(self, tmp_path: Path) -> None:
        tools = _get_tools()
        _seed_learning(tools, impact=0.9)

        with patch(
            "trw_mcp.state.claude_md.resolve_project_root",
            return_value=tmp_path,
        ):
            result = instructions_sync_fn()

        assert "status" in result
        assert result["status"] in ("success", "synced")
        assert "learnings_promoted" in result
        assert isinstance(result["learnings_promoted"], int)
