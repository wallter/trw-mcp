"""Offline parity tests — verify registry + lifecycle tools work with empty intel cache.

PRD-INFRA-054 FR11: After intelligence code removal, the public registry and
critical lifecycle tools must produce valid responses without a backend
connection and with an empty (or missing) intel-cache.json.
"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest
from tests.conftest import _run_async, make_test_server


def _prepare_trw_dir(tmp_path: Path) -> Path:
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    (trw_dir / "context").mkdir()
    (trw_dir / "intel-cache.json").write_text("{}", encoding="utf-8")
    return trw_dir


def _tool_patches(trw_dir: Path) -> tuple[object, ...]:
    return (
        patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.tools.telemetry.resolve_trw_dir", return_value=trw_dir),
    )


class TestOfflineParity:
    """All MCP tools work with empty intelligence cache (no backend)."""

    def test_session_start_empty_cache(self, tmp_path: Path) -> None:
        """trw_session_start runs through the registered tool wrapper offline."""
        trw_dir = _prepare_trw_dir(tmp_path)
        server = make_test_server("ceremony")

        with ExitStack() as stack:
            for context_manager in _tool_patches(trw_dir):
                stack.enter_context(context_manager)
            stack.enter_context(
                patch("trw_mcp.tools.ceremony.find_active_run", return_value=None)
            )
            stack.enter_context(
                patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[])
            )
            stack.enter_context(
                patch("trw_mcp.state.memory_adapter.list_active_learnings", return_value=[])
            )
            result = _run_async(server.call_tool("trw_session_start", {"query": "test"}))

        payload = result.structured_content
        assert payload["success"] is True
        assert payload["query"] == "test"
        assert isinstance(payload["ceremony_status"], str)

    def test_recall_empty_cache(self, tmp_path: Path) -> None:
        """trw_recall works via the registered tool wrapper with neutral intel_boost."""
        trw_dir = _prepare_trw_dir(tmp_path)
        server = make_test_server("learning")

        with ExitStack() as stack:
            for context_manager in _tool_patches(trw_dir):
                stack.enter_context(context_manager)
            _run_async(
                server.call_tool(
                    "trw_learn",
                    {"summary": "test learning", "detail": "test detail"},
                )
            )
            result = _run_async(server.call_tool("trw_recall", {"query": "test"}))

        payload = result.structured_content
        assert payload["query"] == "test"
        assert isinstance(payload["learnings"], list)
        assert payload["learnings"]

    def test_learn_empty_cache(self, tmp_path: Path) -> None:
        """trw_learn records a learning via the registered tool wrapper offline."""
        trw_dir = _prepare_trw_dir(tmp_path)
        server = make_test_server("learning")

        with ExitStack() as stack:
            for context_manager in _tool_patches(trw_dir):
                stack.enter_context(context_manager)
            result = _run_async(
                server.call_tool(
                    "trw_learn",
                    {"summary": "test learning", "detail": "test detail"},
                )
            )

        payload = result.structured_content
        assert payload["status"] == "recorded"
        assert isinstance(payload["learning_id"], str)

    def test_nudge_selection_deterministic_without_bandit(self) -> None:
        """Nudge selection uses deterministic ranking without local policy code."""
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
        """_session_recall_helpers works offline without backend-only intelligence."""
        trw_dir = _prepare_trw_dir(tmp_path)

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
            assert isinstance(learnings, list)

    def test_deliver_empty_cache(self, tmp_path: Path) -> None:
        """trw_deliver succeeds offline without any backend-only intelligence."""
        trw_dir = _prepare_trw_dir(tmp_path)
        server = make_test_server("ceremony")

        with ExitStack() as stack:
            for context_manager in _tool_patches(trw_dir):
                stack.enter_context(context_manager)
            stack.enter_context(
                patch("trw_mcp.tools.ceremony.find_active_run", return_value=None)
            )
            result = _run_async(server.call_tool("trw_deliver", {}))

        payload = result.structured_content
        assert payload["success"] is True
        assert payload["errors"] == []

    def test_no_meta_tune_in_tool_registry(self) -> None:
        """The full tool registry loads offline and excludes trw_meta_tune."""
        # Import must succeed
        import trw_mcp  # noqa: F401

        # Check that meta_tune is not in the registered tools
        from trw_mcp.models.config._defaults import TOOL_PRESETS
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

        assert len(tool_names) == 25
        assert set(tool_names) == set(TOOL_PRESETS["all"])
        assert "trw_meta_tune" not in tool_names
