"""Run report integration and graceful degradation tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.state.persistence import FileStateReader
from trw_mcp.state.report import assemble_report

from ._run_report_support import minimal_run_dir, report_run_dir  # noqa: F401


class TestToolIntegration:
    """Integration tests for assemble_report."""

    def test_full_report(
        self,
        report_run_dir: Path,
        tmp_path: Path,
    ) -> None:
        """Full report with all data sources populated."""
        mock_learnings: list[dict[str, object]] = [
            {
                "id": "L-aaa",
                "summary": "Test learning A",
                "detail": "Detail A",
                "impact": 0.9,
                "tags": ["testing", "report"],
                "created": "2026-02-19",
                "status": "active",
            },
            {
                "id": "L-bbb",
                "summary": "Test learning B",
                "detail": "Detail B",
                "impact": 0.4,
                "tags": ["report"],
                "created": "2026-02-19",
                "status": "active",
            },
        ]
        reader = FileStateReader()
        trw_dir = tmp_path / ".trw"
        with patch("trw_mcp.state.report.list_active_learnings", return_value=mock_learnings):
            report = assemble_report(report_run_dir, reader, trw_dir)

        assert report.run_id == "20260219T100000Z-aaaa1111"
        assert report.task == "analytics-task"
        assert report.status == "complete"
        assert report.phase == "deliver"
        assert report.framework == "v24.0_TRW"
        assert report.prd_scope == ["PRD-CORE-030"]
        assert report.event_summary.total_count == 10
        assert report.event_summary.by_type["phase_enter"] == 6
        assert report.event_summary.by_type["phase_revert"] == 1
        assert len(report.phase_timeline) == 6
        assert report.duration.start_ts == "2026-02-19T10:00:00Z"
        assert report.duration.end_ts == "2026-02-19T13:00:00Z"
        assert report.duration.elapsed_seconds == 10800.0
        assert report.checkpoint_count == 2
        assert report.build is not None
        assert report.build.tests_passed is True
        assert report.build.mypy_clean is True
        assert report.build.coverage_pct == 92.5
        assert report.build.test_count == 45
        assert report.reversion_rate == pytest.approx(0.1429, abs=0.001)
        assert report.learning_summary.total_produced == 2
        assert report.learning_summary.high_impact_count == 1
        assert report.generated_at

    def test_minimal_run_only_run_yaml(
        self,
        minimal_run_dir: Path,
        tmp_path: Path,
    ) -> None:
        """Report generates successfully with only run.yaml (FR05)."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        reader = FileStateReader()
        with patch("trw_mcp.state.report.list_active_learnings", return_value=[]):
            report = assemble_report(minimal_run_dir, reader, trw_dir)

        assert report.run_id == "20260219T080000Z-bbbb2222"
        assert report.event_summary.total_count == 0
        assert report.checkpoint_count == 0
        assert report.build is None
        assert report.learning_summary.total_produced == 0
        assert report.reversion_rate == 0.0

    def test_model_dump_serializable(
        self,
        report_run_dir: Path,
        tmp_path: Path,
    ) -> None:
        """Report model_dump() produces JSON-serializable dict."""
        reader = FileStateReader()
        trw_dir = tmp_path / ".trw"
        with patch("trw_mcp.state.report.list_active_learnings", return_value=[]):
            report = assemble_report(report_run_dir, reader, trw_dir)

        dumped = report.model_dump()
        json_str = json.dumps(dumped)
        assert "20260219T100000Z-aaaa1111" in json_str


class TestGracefulDegradation:
    """Tests for graceful handling of missing/partial data."""

    def test_missing_build_status(
        self,
        minimal_run_dir: Path,
        tmp_path: Path,
    ) -> None:
        """Missing build-status.yaml results in build=None."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        reader = FileStateReader()
        with patch("trw_mcp.state.report.list_active_learnings", return_value=[]):
            report = assemble_report(minimal_run_dir, reader, trw_dir)

        assert report.build is None

    def test_missing_events_file(
        self,
        minimal_run_dir: Path,
        tmp_path: Path,
    ) -> None:
        """Missing events.jsonl results in zero event counts."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        reader = FileStateReader()
        with patch("trw_mcp.state.report.list_active_learnings", return_value=[]):
            report = assemble_report(minimal_run_dir, reader, trw_dir)

        assert report.event_summary.total_count == 0
        assert report.event_summary.by_type == {}

    def test_missing_checkpoints(
        self,
        minimal_run_dir: Path,
        tmp_path: Path,
    ) -> None:
        """Missing checkpoints.jsonl results in checkpoint_count=0."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        reader = FileStateReader()
        with patch("trw_mcp.state.report.list_active_learnings", return_value=[]):
            report = assemble_report(minimal_run_dir, reader, trw_dir)

        assert report.checkpoint_count == 0

    def test_missing_learnings_dir(
        self,
        minimal_run_dir: Path,
        tmp_path: Path,
    ) -> None:
        """Missing learnings in SQLite results in zero learning yield."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        reader = FileStateReader()
        with patch("trw_mcp.state.report.list_active_learnings", return_value=[]):
            report = assemble_report(minimal_run_dir, reader, trw_dir)

        assert report.learning_summary.total_produced == 0
