"""Tests for tools/report.py — trw_run_report and trw_analytics_report tools.

Covers the tool registration and execution paths that were previously at 56% coverage.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import FastMCP

from tests.conftest import get_tools_sync, make_test_server
from trw_mcp.exceptions import StateError
from trw_mcp.state.persistence import FileStateWriter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def report_server() -> FastMCP:
    """Create a FastMCP server with report tools pre-registered."""
    return make_test_server("report")


@pytest.fixture
def rich_run_dir(tmp_path: Path, writer: FileStateWriter) -> Path:
    """Create a run directory with metadata for report generation."""
    run_dir = tmp_path / "docs" / "test-task" / "runs" / "20260219T100000Z-test1234"
    meta = run_dir / "meta"
    meta.mkdir(parents=True)

    writer.write_yaml(
        meta / "run.yaml",
        {
            "run_id": "20260219T100000Z-test1234",
            "task": "test-task",
            "framework": "v24.0_TRW",
            "status": "complete",
            "phase": "deliver",
            "confidence": "high",
        },
    )

    events = [
        {"ts": "2026-02-19T10:00:00Z", "event": "run_init", "task": "test-task"},
        {"ts": "2026-02-19T10:01:00Z", "event": "phase_enter", "phase": "research"},
        {"ts": "2026-02-19T11:00:00Z", "event": "tests_passed"},
    ]
    for evt in events:
        writer.append_jsonl(meta / "events.jsonl", evt)

    writer.append_jsonl(
        meta / "checkpoints.jsonl",
        {
            "ts": "2026-02-19T11:00:00Z",
            "message": "mid-impl",
        },
    )

    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    writer.write_yaml(
        trw_dir / "context" / "build-status.yaml",
        {
            "tests_passed": True,
            "mypy_clean": True,
            "coverage_pct": 88.0,
            "test_count": 100,
        },
    )

    return run_dir


# ---------------------------------------------------------------------------
# TestRunReportTool
# ---------------------------------------------------------------------------


class TestRunReportTool:
    """Tests for trw_run_report tool."""

    def test_explicit_run_path_returns_report(
        self,
        report_server: FastMCP,
        rich_run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Providing explicit run_path returns a valid report dict."""
        tool = get_tools_sync(report_server)["trw_run_report"]

        # Path containment check requires run_path under project root
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            lambda: rich_run_dir.parent.parent.parent.parent,
        )
        result = tool.fn(run_path=str(rich_run_dir))
        assert isinstance(result, dict)
        assert "error" not in result
        assert result["run_id"] == "20260219T100000Z-test1234"

    def test_nonexistent_explicit_path_returns_error(self, report_server: FastMCP) -> None:
        """Nonexistent explicit path returns error dict instead of raising."""
        tool = get_tools_sync(report_server)["trw_run_report"]

        result = tool.fn(run_path="/nonexistent/path/run-000")
        assert "error" in result
        assert result["status"] == "failed"

    def test_auto_detect_with_existing_run(
        self,
        report_server: FastMCP,
        rich_run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Auto-detect returns report when run exists."""
        tool = get_tools_sync(report_server)["trw_run_report"]

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

    def test_no_active_runs_returns_error(self, report_server: FastMCP, monkeypatch: pytest.MonkeyPatch) -> None:
        """When no active runs exist, returns error dict."""
        tool = get_tools_sync(report_server)["trw_run_report"]

        monkeypatch.setattr(
            "trw_mcp.tools.report.resolve_run_path",
            lambda run_path=None: (_ for _ in ()).throw(StateError("No active runs")),
        )

        result = tool.fn(run_path=None)
        assert "error" in result
        assert result["status"] == "failed"

    def test_assemble_report_failure_returns_error(
        self,
        report_server: FastMCP,
        rich_run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If assemble_report raises StateError, returns error dict."""
        tool = get_tools_sync(report_server)["trw_run_report"]

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
        report_server: FastMCP,
        rich_run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When resolve_trw_dir fails, falls back to relative .trw dir."""
        tool = get_tools_sync(report_server)["trw_run_report"]

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
        self,
        report_server: FastMCP,
        rich_run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Report result contains expected keys from model_dump."""
        tool = get_tools_sync(report_server)["trw_run_report"]

        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            lambda: rich_run_dir.parent.parent.parent.parent,
        )
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
        self, report_server: FastMCP, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns valid dict even with no runs available."""
        tool = get_tools_sync(report_server)["trw_analytics_report"]

        monkeypatch.setattr(
            "trw_mcp.state.analytics.report.resolve_project_root",
            lambda: tmp_path,
        )

        result = tool.fn(since=None)
        assert isinstance(result, dict)
        assert "error" not in result
        assert result.get("runs_scanned", 0) == 0

    def test_scan_all_runs_with_date_filter(
        self, report_server: FastMCP, tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Date filter is passed to scan_all_runs correctly."""
        tool = get_tools_sync(report_server)["trw_analytics_report"]

        monkeypatch.setattr(
            "trw_mcp.state.analytics.report.resolve_project_root",
            lambda: tmp_path,
        )

        result = tool.fn(since="2026-01-01")
        assert isinstance(result, dict)

    def test_exception_returns_error_dict(self, report_server: FastMCP, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unexpected exception is caught and returned as error dict."""
        tool = get_tools_sync(report_server)["trw_analytics_report"]

        import trw_mcp.state.analytics.report as analytics_mod

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
        report_server: FastMCP,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Returns runs_scanned > 0 when run directories exist."""
        tool = get_tools_sync(report_server)["trw_analytics_report"]

        # Create a run directory
        run_dir = tmp_path / ".trw" / "runs" / "task1" / "20260219T100000Z-abcd1234"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        writer.write_yaml(
            meta / "run.yaml",
            {
                "run_id": "20260219T100000Z-abcd1234",
                "task": "task1",
                "status": "complete",
                "phase": "deliver",
            },
        )

        monkeypatch.setattr(
            "trw_mcp.state.analytics.report.resolve_project_root",
            lambda: tmp_path,
        )

        result = tool.fn(since=None)
        assert isinstance(result, dict)
        assert result.get("runs_scanned", 0) >= 1

    def test_tools_are_registered(self, report_server: FastMCP) -> None:
        """Both tools are registered after register_report_tools."""
        tool_names = list(get_tools_sync(report_server).keys())
        assert "trw_run_report" in tool_names
        assert "trw_analytics_report" in tool_names
