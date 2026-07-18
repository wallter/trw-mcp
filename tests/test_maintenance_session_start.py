"""Tests for session_start maintenance integration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.conftest import get_tools_sync
from trw_mcp.models.config import TRWConfig


class TestSessionStartAutoClose:
    """trw_session_start Step 5: auto_close_stale_runs integration."""

    @staticmethod
    def _get_session_start_fn() -> object:
        """Register ceremony tools on a minimal FastMCP server and return the tool."""
        from fastmcp import FastMCP

        from trw_mcp.tools.ceremony import register_ceremony_tools

        server = FastMCP("test")
        register_ceremony_tools(server)
        tool = get_tools_sync(server)["trw_session_start"]
        return getattr(tool, "fn", tool)

    def test_session_start_calls_auto_close_when_enabled(
        self,
        tmp_path: Path,
    ) -> None:
        """When run_auto_close_enabled=True, auto_close_stale_runs is called and
        the result is surfaced in the return value when count > 0."""
        cfg = TRWConfig()
        object.__setattr__(cfg, "run_auto_close_enabled", True)

        import trw_mcp.state.analytics._stale_runs as stale_mod

        close_result = {"runs_closed": ["run-001"], "count": 1, "errors": []}
        original_fn = stale_mod.auto_close_stale_runs
        mock_close = MagicMock(return_value=close_result)

        fn = self._get_session_start_fn()

        try:
            stale_mod.auto_close_stale_runs = mock_close
            with (
                patch("trw_mcp.tools.ceremony.get_config", return_value=cfg),
                patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=tmp_path / ".trw"),
                patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
                patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]),
                patch("trw_mcp.tools.ceremony._events"),
            ):
                result = fn()
        finally:
            stale_mod.auto_close_stale_runs = original_fn

        mock_close.assert_called_once()
        assert result.get("stale_runs_closed") == close_result

    def test_session_start_does_not_call_auto_close_when_disabled(
        self,
        tmp_path: Path,
    ) -> None:
        """When run_auto_close_enabled=False, auto_close_stale_runs is never called."""
        cfg = TRWConfig()
        object.__setattr__(cfg, "run_auto_close_enabled", False)

        mock_close = MagicMock(return_value={"runs_closed": [], "count": 0, "errors": []})
        with (
            patch("trw_mcp.tools.ceremony.get_config", return_value=cfg),
            patch("trw_mcp.models.config.get_config", return_value=cfg),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]),
            patch("trw_mcp.tools.ceremony._events"),
            patch("trw_mcp.state.analytics.report.auto_close_stale_runs", mock_close),
        ):
            fn = self._get_session_start_fn()
            result = fn()

        mock_close.assert_not_called()
        assert "stale_runs_closed" not in result

    def test_session_start_auto_close_exception_is_fail_open(
        self,
        tmp_path: Path,
    ) -> None:
        """If auto_close_stale_runs raises, session_start still succeeds."""
        cfg = TRWConfig()
        object.__setattr__(cfg, "run_auto_close_enabled", True)

        with (
            patch("trw_mcp.tools.ceremony.get_config", return_value=cfg),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]),
            patch("trw_mcp.tools.ceremony._events"),
        ):
            import trw_mcp.state.analytics.report as ar_mod

            original_fn = ar_mod.auto_close_stale_runs
            try:
                ar_mod.auto_close_stale_runs = MagicMock(side_effect=RuntimeError("disk full"))
                fn = self._get_session_start_fn()
                result = fn()
            finally:
                ar_mod.auto_close_stale_runs = original_fn

        assert result is not None
        assert "stale_runs_closed" not in result

    def test_session_start_surfaces_scheduled_embedding_backfill(self, tmp_path: Path) -> None:
        """The public compact result preserves actionable maintenance remediation."""
        cfg = TRWConfig()
        scheduled = {"reason": "low_coverage", "thread_started": True}

        with (
            patch("trw_mcp.tools.ceremony.get_config", return_value=cfg),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]),
            patch("trw_mcp.tools.ceremony._events"),
            patch(
                "trw_mcp.tools._ceremony_helpers.step_sanitize_and_maintain",
                return_value={"embeddings_backfill_scheduled": scheduled},
            ),
        ):
            result = self._get_session_start_fn()()

        assert result["embeddings_backfill_scheduled"] == scheduled
