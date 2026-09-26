"""Offline parity tests — verify registry + lifecycle tools work with empty intel cache.

PRD-INFRA-054 FR11: After intelligence code removal, the public registry and
critical lifecycle tools must produce valid responses without a backend
connection and with an empty (or missing) intel-cache.json.
"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from tests._memory_fixtures import DaemonCheckout
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
        assert payload["success"] is True, payload.get("errors")
        assert payload["query"] == "test"
        assert isinstance(payload["ceremony_status"], str)

    def test_recall_empty_cache(self, daemon_checkout: DaemonCheckout) -> None:
        """trw_recall works via the registered tool wrapper with an empty intelligence cache."""
        trw_dir = daemon_checkout.trw_dir
        (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
        (trw_dir / "context").mkdir(exist_ok=True)
        (trw_dir / "intel-cache.json").write_text("{}", encoding="utf-8")
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

    def test_learn_empty_cache(self, tmp_path: Path, fake_memory_store: object) -> None:
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
        ):
            from trw_mcp.models.config import TRWConfig
            from trw_mcp.state.persistence import FileStateReader
            from trw_mcp.tools._session_recall_helpers import perform_session_recalls

            config = TRWConfig(trw_dir=str(trw_dir))
            reader = FileStateReader()
            learnings, extras = perform_session_recalls(trw_dir, "*", config, reader, verbose=True)
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

    def test_every_registered_tool_is_eligible(self) -> None:
        """The tool registry loads offline, and the eligible surface is exactly the
        registered tools. PRD-CORE-218: tools are registered then MASKED per
        session by SurfaceAuthorityMiddleware, so this checks the manifest
        authority, not a boot-time preset filter. PRD-CORE-300 S3a deleted the
        operator-only tier that used to be registered but never eligible.
        """
        import trw_mcp  # noqa: F401
        from trw_mcp.server._surface_manifest_registry import eligible_tool_names
        from trw_mcp.server._tools import raw_registered_tool_names

        assert set(eligible_tool_names()) == raw_registered_tool_names()


class TestStaleStoreErrorIsolation:
    """T26: a store failure left in the context by an EARLIER recall is not this call's failure.

    ``_STORE_ERROR`` is a ContextVar; a synchronous recall in the same context (another tool, or
    another test on the same xdist worker) used to leave it set, and the next session_start
    reported it as its own critical recall failure (``success: false``).
    """

    def test_session_start_ignores_a_stale_store_error(self, tmp_path: Path) -> None:
        from trw_mcp.state._memory_recall import _STORE_ERROR

        trw_dir = _prepare_trw_dir(tmp_path)
        server = make_test_server("ceremony")
        _STORE_ERROR.set("memory store at /elsewhere could not be opened: stale")
        try:
            with ExitStack() as stack:
                for context_manager in _tool_patches(trw_dir):
                    stack.enter_context(context_manager)
                stack.enter_context(patch("trw_mcp.tools.ceremony.find_active_run", return_value=None))
                stack.enter_context(patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]))
                stack.enter_context(patch("trw_mcp.state.memory_adapter.list_active_learnings", return_value=[]))
                result = _run_async(server.call_tool("trw_session_start", {"query": "test"}))
        finally:
            _STORE_ERROR.set(None)

        assert result.structured_content["success"] is True, result.structured_content.get("errors")

    def test_recall_ignores_a_stale_store_error(self, tmp_path: Path, fake_memory_store: object) -> None:
        from trw_mcp.state._memory_recall import _STORE_ERROR

        trw_dir = _prepare_trw_dir(tmp_path)
        server = make_test_server("learning")
        _STORE_ERROR.set("memory store at /elsewhere could not be opened: stale")
        try:
            with ExitStack() as stack:
                for context_manager in _tool_patches(trw_dir):
                    stack.enter_context(context_manager)
                result = _run_async(server.call_tool("trw_recall", {"query": "test"}))
        finally:
            _STORE_ERROR.set(None)

        assert "store_unavailable" not in result.structured_content
