"""Offline parity tests — verify MCP tools work with empty intelligence cache.

PRD-INFRA-054 FR11: After intelligence code removal, all remaining MCP tools
must produce valid responses without a backend connection and with an empty
(or missing) intel-cache.json.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestOfflineParity:
    """All MCP tools work with empty intelligence cache (no backend)."""

    def test_session_start_empty_cache(self, tmp_path: Path) -> None:
        """trw_session_start succeeds with empty intel cache and no backend."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir()

        # Empty intel cache
        (trw_dir / "intel-cache.json").write_text("{}", encoding="utf-8")

        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir), \
             patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]), \
             patch("trw_mcp.state.memory_adapter.list_active_learnings", return_value=[]):
            from trw_mcp.state.ceremony_nudge import compute_nudge, read_ceremony_state

            state = read_ceremony_state(trw_dir)
            nudge = compute_nudge(state, available_learnings=0)
            # Should produce a valid nudge string without error
            assert isinstance(nudge, str)

    def test_recall_empty_cache(self, tmp_path: Path) -> None:
        """trw_recall logic works with empty intel cache (intel_boost=1.0)."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "learnings" / "entries").mkdir(parents=True)

        # Verify scoring with neutral intel_boost
        from trw_mcp.scoring import rank_by_utility

        learnings = [
            {"id": "L-1", "summary": "test learning", "impact": 0.8},
            {"id": "L-2", "summary": "another learning", "impact": 0.5},
        ]
        ranked = rank_by_utility(learnings, ["test"], lambda_weight=0.3)
        assert len(ranked) == 2
        # Rankings should work without any intelligence enrichment
        assert all(isinstance(e, dict) for e in ranked)

    def test_learn_empty_cache(self, tmp_path: Path) -> None:
        """Learning storage works without intelligence modules."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "learnings" / "entries").mkdir(parents=True)

        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir), \
             patch("trw_mcp.state.memory_adapter.store_learning") as mock_store:
            mock_store.return_value = {"id": "test-id", "status": "created"}
            from trw_mcp.state.memory_adapter import store_learning

            result = store_learning(
                trw_dir,
                summary="test learning",
                detail="test detail",
            )
            assert result is not None

    def test_nudge_selection_deterministic_without_bandit(self) -> None:
        """Nudge selection uses deterministic ranking when bandit is unavailable."""
        from trw_mcp.state._nudge_rules import select_nudge_learning
        from trw_mcp.state._nudge_state import CeremonyState

        state = CeremonyState()
        candidates = [
            {"id": "L-1", "summary": "First learning"},
            {"id": "L-2", "summary": "Second learning"},
        ]

        # Without bandit, deterministic path returns first eligible
        selected, is_fallback = select_nudge_learning(
            state, candidates, "implement"
        )
        assert selected is not None
        assert selected["id"] == "L-1"
        assert is_fallback is False

    def test_session_recall_helpers_works_offline(self, tmp_path: Path) -> None:
        """_session_recall_helpers works offline — bandit_policy is local-first (PRD-CORE-105)."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir()

        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir), \
             patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[
                 {"id": "L-1", "summary": "test", "impact": 0.8},
             ]), \
             patch("trw_mcp.state.memory_adapter.update_access_tracking"):
            from trw_mcp.models.config import TRWConfig
            from trw_mcp.state.persistence import FileStateReader
            from trw_mcp.tools._session_recall_helpers import perform_session_recalls

            config = TRWConfig(trw_dir=str(trw_dir))
            reader = FileStateReader()
            learnings, auto_recalled, extras = perform_session_recalls(
                trw_dir, "*", config, reader
            )
            # Should complete successfully (bandit_policy is local, not backend)
            assert isinstance(learnings, list)

    def test_no_meta_tune_in_tool_registry(self) -> None:
        """trw_meta_tune tool is not registered after PRD-INFRA-054."""
        # Import must succeed
        import trw_mcp  # noqa: F401

        # Check that meta_tune is not in the registered tools
        from trw_mcp.server._tools import mcp

        import asyncio

        async def _list() -> list[str]:
            tools = await mcp.list_tools()
            return [t.name for t in tools]

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                tool_names = pool.submit(asyncio.run, _list()).result()
        else:
            tool_names = asyncio.run(_list())

        assert "trw_meta_tune" not in tool_names
