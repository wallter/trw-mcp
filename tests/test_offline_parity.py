"""Offline parity tests — verify registry + lifecycle tools work with empty intel cache.

PRD-INFRA-054 FR11: After intelligence code removal, the public registry and
critical lifecycle tools must produce valid responses without a backend
connection and with an empty (or missing) intel-cache.json.
"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

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
            stack.enter_context(patch("trw_mcp.tools.ceremony.find_active_run", return_value=None))
            stack.enter_context(patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]))
            stack.enter_context(patch("trw_mcp.state.memory_adapter.list_active_learnings", return_value=[]))
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

    def test_session_recall_helpers_works_offline(self, tmp_path: Path) -> None:
        """_session_recall_helpers works offline without backend-only intelligence."""
        trw_dir = _prepare_trw_dir(tmp_path)

        with (
            patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir),
            patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                return_value=[
                    {"id": "L-1", "summary": "test", "impact": 0.8},
                ],
            ),
            patch("trw_mcp.state.memory_adapter.update_access_tracking"),
        ):
            from trw_mcp.models.config import TRWConfig
            from trw_mcp.state.persistence import FileStateReader
            from trw_mcp.tools._session_recall_helpers import perform_session_recalls

            config = TRWConfig(trw_dir=str(trw_dir))
            reader = FileStateReader()
            learnings, auto_recalled, extras = perform_session_recalls(trw_dir, "*", config, reader)
            assert isinstance(learnings, list)

    def test_deliver_empty_cache(self, tmp_path: Path) -> None:
        """trw_deliver succeeds offline without any backend-only intelligence."""
        trw_dir = _prepare_trw_dir(tmp_path)
        server = make_test_server("ceremony")

        with ExitStack() as stack:
            for context_manager in _tool_patches(trw_dir):
                stack.enter_context(context_manager)
            stack.enter_context(patch("trw_mcp.tools.ceremony.find_active_run", return_value=None))
            result = _run_async(server.call_tool("trw_deliver", {}))

        payload = result.structured_content
        assert payload["success"] is True
        assert payload["errors"] == []

    def test_meta_tune_propose_is_operator_only(self) -> None:
        """The tool registry loads offline; the self-modifying trw_meta_tune_propose
        is operator-only (never in the eligible public surface). PRD-CORE-218:
        tools are registered then MASKED per session by SurfaceAuthorityMiddleware,
        so this checks the manifest authority, not a boot-time preset filter."""
        import trw_mcp  # noqa: F401
        from trw_mcp.models.surface_packs import OPERATOR_ONLY_TOOLS
        from trw_mcp.server._surface_manifest_registry import eligible_tool_names
        from trw_mcp.server._tools import raw_registered_tool_names

        registered = raw_registered_tool_names()
        eligible = set(eligible_tool_names())

        # meta_tune_propose is REGISTERED (grantable) but NOT in the eligible surface.
        assert "trw_meta_tune_propose" in registered
        assert "trw_meta_tune_propose" not in eligible
        assert "trw_meta_tune_propose" in OPERATOR_ONLY_TOOLS
        # The eligible surface is exactly the registered tools minus operator-only.
        assert eligible == registered - set(OPERATOR_ONLY_TOOLS)
