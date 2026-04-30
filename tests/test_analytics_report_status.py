"""Tests for stale-run status reporting integration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests.conftest import get_tools_sync


class TestStaleCountInStatus:
    """trw_status includes stale_count in its response."""

    def test_stale_count_in_status(self, tmp_path: Path, sample_run_dir: Path) -> None:
        """trw_status response includes stale_count field."""
        from fastmcp import FastMCP

        from trw_mcp.tools.orchestration import register_orchestration_tools

        server = FastMCP("test")
        register_orchestration_tools(server)

        tools = get_tools_sync(server)
        status_tool = tools["trw_status"]

        with (
            patch(
                "trw_mcp.tools.orchestration.resolve_run_path",
                return_value=sample_run_dir,
            ),
            patch(
                "trw_mcp.tools.orchestration.count_stale_runs",
                return_value=3,
            ) as mock_count,
        ):
            result = status_tool.fn()

        assert "stale_count" in result
        assert result["stale_count"] == 3
        assert "stale_runs_advisory" in result
        assert "3 stale run(s)" in str(result["stale_runs_advisory"])
        mock_count.assert_called_once()

    def test_stale_count_zero_no_advisory(self, tmp_path: Path, sample_run_dir: Path) -> None:
        """When stale count is 0, no advisory is shown."""
        from fastmcp import FastMCP

        from trw_mcp.tools.orchestration import register_orchestration_tools

        server = FastMCP("test")
        register_orchestration_tools(server)

        tools = get_tools_sync(server)
        status_tool = tools["trw_status"]

        with (
            patch(
                "trw_mcp.tools.orchestration.resolve_run_path",
                return_value=sample_run_dir,
            ),
            patch(
                "trw_mcp.tools.orchestration.count_stale_runs",
                return_value=0,
            ),
        ):
            result = status_tool.fn()

        assert result["stale_count"] == 0
        assert "stale_runs_advisory" not in result

    def test_stale_count_error_failopen(self, tmp_path: Path, sample_run_dir: Path) -> None:
        """When count_stale_runs raises, trw_status still returns normally."""
        from fastmcp import FastMCP

        from trw_mcp.tools.orchestration import register_orchestration_tools

        server = FastMCP("test")
        register_orchestration_tools(server)

        tools = get_tools_sync(server)
        status_tool = tools["trw_status"]

        with (
            patch(
                "trw_mcp.tools.orchestration.resolve_run_path",
                return_value=sample_run_dir,
            ),
            patch(
                "trw_mcp.tools.orchestration.count_stale_runs",
                side_effect=OSError("disk failure"),
            ),
        ):
            result = status_tool.fn()

        assert "run_id" in result
        assert "stale_count" not in result
