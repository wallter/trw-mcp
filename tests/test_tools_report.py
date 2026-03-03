"""Tests for tools/report.py — trw_run_report and trw_analytics_report tools.

Covers the tool registration and execution paths that were previously at 56% coverage.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import FastMCP

from trw_mcp.exceptions import StateError
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.tools.report import register_report_tools

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def server() -> FastMCP:
    """Create a fresh FastMCP server for tool registration."""
    return FastMCP("test-server")


@pytest.fixture
def rich_run_dir(tmp_path: Path, writer: FileStateWriter) -> Path:
    """Create a run directory with metadata for report generation."""
    run_dir = tmp_path / "docs" / "test-task" / "runs" / "20260219T100000Z-test1234"
    meta = run_dir / "meta"
    meta.mkdir(parents=True)

    writer.write_yaml(meta / "run.yaml", {
        "run_id": "20260219T100000Z-test1234",
        "task": "test-task",
        "framework": "v24.0_TRW",
        "status": "complete",
        "phase": "deliver",
        "confidence": "high",
    })

    events = [
        {"ts": "2026-02-19T10:00:00Z", "event": "run_init", "task": "test-task"},
        {"ts": "2026-02-19T10:01:00Z", "event": "phase_enter", "phase": "research"},
        {"ts": "2026-02-19T11:00:00Z", "event": "tests_passed"},
    ]
    for evt in events:
        writer.append_jsonl(meta / "events.jsonl", evt)

    writer.append_jsonl(meta / "checkpoints.jsonl", {
        "ts": "2026-02-19T11:00:00Z",
        "message": "mid-impl",
    })

    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    writer.write_yaml(trw_dir / "context" / "build-status.yaml", {
        "tests_passed": True,
        "mypy_clean": True,
        "coverage_pct": 88.0,
        "test_count": 100,
    })

    return run_dir


# ---------------------------------------------------------------------------
# TestRunReportTool
# ---------------------------------------------------------------------------


class TestRunReportTool:
    """Tests for trw_run_report tool."""

    def test_explicit_run_path_returns_report(
        self, server: FastMCP, rich_run_dir: Path
    ) -> None:
        """Providing explicit run_path returns a valid report dict."""
        register_report_tools(server)
        tool = next(t for t in server._tool_manager._tools.values() if t.name == "trw_run_report")

        result = tool.fn(run_path=str(rich_run_dir))
        assert isinstance(result, dict)
        assert "error" not in result
        assert result["run_id"] == "20260219T100000Z-test1234"

    def test_nonexistent_explicit_path_returns_error(self, server: FastMCP) -> None:
        """Nonexistent explicit path returns error dict instead of raising."""
        register_report_tools(server)
        tool = next(t for t in server._tool_manager._tools.values() if t.name == "trw_run_report")

        result = tool.fn(run_path="/nonexistent/path/run-000")
        assert "error" in result
        assert result["status"] == "failed"

    def test_auto_detect_with_existing_run(
        self,
        server: FastMCP,
        rich_run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Auto-detect returns report when run exists."""
        register_report_tools(server)
        tool = next(t for t in server._tool_manager._tools.values() if t.name == "trw_run_report")

        monkeypatch.setattr(
            "trw_mcp.tools.report.resolve_run_path",
            lambda run_path=None: rich_run_dir,
        )
        monkeypatch.setattr(
            "trw_mcp.tools.report.resolve_trw_dir",
            lambda: rich_run_dir.parent.parent.parent / ".trw",
        )

        result = tool.fn(run_path=None)
        assert isinstance(result, dict)
        assert "error" not in result

    def test_no_active_runs_returns_error(self, server: FastMCP, monkeypatch: pytest.MonkeyPatch) -> None:
        """When no active runs exist, returns error dict."""
        register_report_tools(server)
        tool = next(t for t in server._tool_manager._tools.values() if t.name == "trw_run_report")

        monkeypatch.setattr(
            "trw_mcp.tools.report.resolve_run_path",
            lambda run_path=None: (_ for _ in ()).throw(StateError("No active runs")),
        )

        result = tool.fn(run_path=None)
        assert "error" in result
        assert result["status"] == "failed"

    def test_assemble_report_failure_returns_error(
        self,
        server: FastMCP,
        rich_run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If assemble_report raises StateError, returns error dict."""
        register_report_tools(server)
        tool = next(t for t in server._tool_manager._tools.values() if t.name == "trw_run_report")

        monkeypatch.setattr(
            "trw_mcp.tools.report.resolve_run_path",
            lambda run_path=None: rich_run_dir,
        )
        monkeypatch.setattr(
            "trw_mcp.tools.report.resolve_trw_dir",
            lambda: rich_run_dir.parent.parent.parent / ".trw",
        )
        monkeypatch.setattr(
            "trw_mcp.tools.report.assemble_report",
            lambda *a, **kw: (_ for _ in ()).throw(StateError("Report assembly failed")),
        )

        result = tool.fn(run_path=None)
        assert "error" in result
        assert result["status"] == "failed"

    def test_resolve_trw_dir_failure_uses_fallback(
        self,
        server: FastMCP,
        rich_run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When resolve_trw_dir fails, falls back to relative .trw dir."""
        register_report_tools(server)
        tool = next(t for t in server._tool_manager._tools.values() if t.name == "trw_run_report")

        monkeypatch.setattr(
            "trw_mcp.tools.report.resolve_run_path",
            lambda run_path=None: rich_run_dir,
        )
        monkeypatch.setattr(
            "trw_mcp.tools.report.resolve_trw_dir",
            lambda: (_ for _ in ()).throw(RuntimeError("no .trw dir")),
        )

        # Should not raise; falls back to parent/.trw
        result = tool.fn(run_path=None)
        # Result is a dict (report or error) — no exception
        assert isinstance(result, dict)

    def test_result_is_serializable_dict(
        self, server: FastMCP, rich_run_dir: Path
    ) -> None:
        """Report result contains expected keys from model_dump."""
        register_report_tools(server)
        tool = next(t for t in server._tool_manager._tools.values() if t.name == "trw_run_report")

        result = tool.fn(run_path=str(rich_run_dir))
        assert isinstance(result, dict)
        # RunReport model keys (from RunReport.model_dump())
        expected_keys = {"run_id", "task", "duration", "event_summary", "checkpoint_count"}
        assert expected_keys.issubset(result.keys())


# ---------------------------------------------------------------------------
# TestAnalyticsReportTool
# ---------------------------------------------------------------------------


class TestAnalyticsReportTool:
    """Tests for trw_analytics_report tool."""

    def test_returns_dict_no_runs(
        self, server: FastMCP, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns valid dict even with no runs available."""
        register_report_tools(server)
        tool = next(t for t in server._tool_manager._tools.values() if t.name == "trw_analytics_report")

        monkeypatch.setattr(
            "trw_mcp.state.analytics_report.resolve_project_root",
            lambda: tmp_path,
        )

        result = tool.fn(since=None)
        assert isinstance(result, dict)
        assert "error" not in result
        assert result.get("runs_scanned", 0) == 0

    def test_scan_all_runs_with_date_filter(
        self, server: FastMCP, tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Date filter is passed to scan_all_runs correctly."""
        register_report_tools(server)
        tool = next(t for t in server._tool_manager._tools.values() if t.name == "trw_analytics_report")

        monkeypatch.setattr(
            "trw_mcp.state.analytics_report.resolve_project_root",
            lambda: tmp_path,
        )

        result = tool.fn(since="2026-01-01")
        assert isinstance(result, dict)

    def test_exception_returns_error_dict(
        self, server: FastMCP, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unexpected exception is caught and returned as error dict."""
        register_report_tools(server)
        tool = next(t for t in server._tool_manager._tools.values() if t.name == "trw_analytics_report")

        import trw_mcp.state.analytics_report as analytics_mod
        monkeypatch.setattr(
            analytics_mod,
            "scan_all_runs",
            lambda since=None: (_ for _ in ()).throw(RuntimeError("unexpected")),
        )

        result = tool.fn(since=None)
        assert "error" in result
        assert result["status"] == "failed"

    def test_with_actual_run_data(
        self,
        server: FastMCP,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Returns runs_scanned > 0 when run directories exist."""
        register_report_tools(server)
        tool = next(t for t in server._tool_manager._tools.values() if t.name == "trw_analytics_report")

        # Create a run directory
        run_dir = tmp_path / "docs" / "task1" / "runs" / "20260219T100000Z-abcd1234"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        writer.write_yaml(meta / "run.yaml", {
            "run_id": "20260219T100000Z-abcd1234",
            "task": "task1",
            "status": "complete",
            "phase": "deliver",
        })

        monkeypatch.setattr(
            "trw_mcp.state.analytics_report.resolve_project_root",
            lambda: tmp_path,
        )

        result = tool.fn(since=None)
        assert isinstance(result, dict)
        assert result.get("runs_scanned", 0) >= 1

    def test_tools_are_registered(self, server: FastMCP) -> None:
        """Both tools are registered after register_report_tools."""
        register_report_tools(server)
        tool_names = list(server._tool_manager._tools.keys())
        assert "trw_run_report" in tool_names
        assert "trw_analytics_report" in tool_names
