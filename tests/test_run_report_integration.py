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


class TestCorruptJsonlResilience:
    """A torn/undecodable line in an advisory append-only log must degrade to
    "drop that line", not crash the whole report (strict read raised StateError).
    """

    def test_torn_events_line_does_not_crash_report(
        self,
        report_run_dir: Path,
        tmp_path: Path,
    ) -> None:
        """A half-written final event line is skipped; valid events still parse."""
        events_path = report_run_dir / "meta" / "events.jsonl"
        with events_path.open("a", encoding="utf-8") as fh:
            fh.write('{"ts": "2026-02-19T13:30:00Z", "event": "phase_en')  # torn append

        reader = FileStateReader()
        trw_dir = tmp_path / ".trw"
        with patch("trw_mcp.state.report.list_active_learnings", return_value=[]):
            report = assemble_report(report_run_dir, reader, trw_dir)

        # All 10 well-formed events survive; the torn 11th line is dropped.
        assert report.event_summary.total_count == 10
        assert report.event_summary.by_type["phase_enter"] == 6

    def test_non_object_events_line_is_skipped(
        self,
        report_run_dir: Path,
        tmp_path: Path,
    ) -> None:
        """A valid-JSON-but-non-object line (e.g. a bare number) is skipped."""
        events_path = report_run_dir / "meta" / "events.jsonl"
        with events_path.open("a", encoding="utf-8") as fh:
            fh.write("12345\n")

        reader = FileStateReader()
        trw_dir = tmp_path / ".trw"
        with patch("trw_mcp.state.report.list_active_learnings", return_value=[]):
            report = assemble_report(report_run_dir, reader, trw_dir)

        assert report.event_summary.total_count == 10

    def test_undecodable_events_line_is_skipped(
        self,
        report_run_dir: Path,
        tmp_path: Path,
    ) -> None:
        """A non-UTF-8 byte row is contained to its own line and skipped."""
        events_path = report_run_dir / "meta" / "events.jsonl"
        with events_path.open("ab") as fh:
            fh.write(b"\xff\xfe not utf-8\n")

        reader = FileStateReader()
        trw_dir = tmp_path / ".trw"
        with patch("trw_mcp.state.report.list_active_learnings", return_value=[]):
            report = assemble_report(report_run_dir, reader, trw_dir)

        assert report.event_summary.total_count == 10

    def test_torn_checkpoints_line_does_not_crash_report(
        self,
        report_run_dir: Path,
        tmp_path: Path,
    ) -> None:
        """A torn checkpoint append is dropped; valid checkpoints still counted."""
        checkpoints_path = report_run_dir / "meta" / "checkpoints.jsonl"
        with checkpoints_path.open("a", encoding="utf-8") as fh:
            fh.write('{"ts": "2026-02-19T13:00:00Z", "messa')  # torn append

        reader = FileStateReader()
        trw_dir = tmp_path / ".trw"
        with patch("trw_mcp.state.report.list_active_learnings", return_value=[]):
            report = assemble_report(report_run_dir, reader, trw_dir)

        # Two well-formed checkpoints survive; the torn line is dropped.
        assert report.checkpoint_count == 2
