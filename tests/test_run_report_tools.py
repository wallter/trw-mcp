"""Run report tool registration tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastmcp import FastMCP

from tests.conftest import get_tools_sync
from trw_mcp.tools.report import register_report_tools


class TestReportToolLayer:
    """Verify trw_run_report and trw_analytics_report are registered and callable."""

    def test_both_tools_registered(self) -> None:
        """Both trw_run_report and trw_analytics_report are discoverable after registration."""
        srv = FastMCP("report-tool-test")
        register_report_tools(srv)
        tools = get_tools_sync(srv)
        assert "trw_run_report" in tools, "trw_run_report not registered"
        assert "trw_analytics_report" in tools, "trw_analytics_report not registered"

    def test_trw_run_report_returns_error_for_missing_run(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """trw_run_report returns error dict when run path cannot be resolved."""
        import trw_mcp.tools.report as report_mod
        from trw_mcp.exceptions import StateError

        def _raise(_: object = None, **__: object) -> None:
            raise StateError("no active run", path="none")

        monkeypatch.setattr(report_mod, "resolve_run_path", _raise)

        srv = FastMCP("run-report-error-test")
        register_report_tools(srv)
        tools = get_tools_sync(srv)
        result = tools["trw_run_report"].fn()
        assert isinstance(result, dict)
        assert result.get("status") == "failed"
        assert "error" in result

    def test_trw_run_report_returns_report_for_valid_run(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """trw_run_report returns a populated report dict for a valid run directory."""
        import trw_mcp.tools.report as report_mod
        from trw_mcp.state.persistence import FileStateWriter

        writer = FileStateWriter()

        run_dir = tmp_path / "docs" / "t" / "runs" / "20260101T000000Z-aaaa1111"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        writer.write_yaml(
            meta / "run.yaml",
            {
                "run_id": "20260101T000000Z-aaaa1111",
                "task": "t",
                "framework": "v24.0_TRW",
                "status": "active",
                "phase": "implement",
                "confidence": "medium",
                "run_type": "implementation",
                "prd_scope": [],
            },
        )
        writer.append_jsonl(
            meta / "events.jsonl",
            {
                "ts": "2026-01-01T00:00:00Z",
                "event": "run_init",
            },
        )

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)

        monkeypatch.setattr(report_mod, "resolve_run_path", lambda _=None, **__: run_dir)
        monkeypatch.setattr(report_mod, "resolve_trw_dir", lambda: trw_dir)

        srv = FastMCP("run-report-valid-test")
        register_report_tools(srv)
        tools = get_tools_sync(srv)
        with patch("trw_mcp.state.report.list_active_learnings", return_value=[]):
            result = tools["trw_run_report"].fn()
        assert isinstance(result, dict)
        assert result.get("run_id") == "20260101T000000Z-aaaa1111"
        assert result.get("task") == "t"
        assert "status" in result
