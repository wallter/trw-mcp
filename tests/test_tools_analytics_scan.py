"""Run scanning tests for analytics report helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

import trw_mcp.state.analytics.report as analytics_mod
from tests._test_tools_analytics_support import _write_run, multi_run_project, writer  # noqa: F401
from trw_mcp.state.analytics.report import scan_all_runs
from trw_mcp.state.persistence import FileStateWriter


class TestScanAllRuns:
    """T-14 through T-17, T-24: scan_all_runs integration tests."""

    def test_three_mock_runs_returns_three_entries(self, multi_run_project: Path) -> None:
        """T-14: 3 mock runs produce 3 entries in scan_all_runs result."""
        result = scan_all_runs()
        runs = result["runs"]
        assert isinstance(runs, list)
        assert len(runs) == 3
        assert result["runs_scanned"] == 3

    def test_since_filter_excludes_older_runs(self, multi_run_project: Path) -> None:
        """T-15: since filter correctly excludes runs before the cutoff date."""
        result = scan_all_runs(since="2026-01-03")
        runs = result["runs"]
        assert len(runs) == 1
        assert "cccc3333" in str(runs[0]["run_id"])

    def test_since_filter_includes_all_when_before_all_runs(self, multi_run_project: Path) -> None:
        """T-15 corollary: since filter before all runs includes all entries."""
        result = scan_all_runs(since="2025-01-01")
        assert len(result["runs"]) == 3

    def test_since_filter_brackets_middle_run(self, multi_run_project: Path) -> None:
        """T-15 corollary: since=Jan 2 includes Jan 2 and Jan 3 runs only."""
        result = scan_all_runs(since="2026-01-02")
        runs = result["runs"]
        assert len(runs) == 2
        run_ids = [str(r["run_id"]) for r in runs]
        assert any("bbbb2222" in rid for rid in run_ids)
        assert any("cccc3333" in rid for rid in run_ids)

    def test_parse_error_listed_not_raised(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-16: Corrupt run.yaml surfaces in parse_errors, does not raise."""
        monkeypatch.setattr(analytics_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(analytics_mod._config, "runs_root", ".trw/runs")
        monkeypatch.setattr(analytics_mod, "resolve_trw_dir", lambda: tmp_path / ".trw")

        corrupt_run_dir = tmp_path / ".trw" / "runs" / "bad-task" / "20260101T000000Z-bad00000" / "meta"
        corrupt_run_dir.mkdir(parents=True)
        (corrupt_run_dir / "run.yaml").write_bytes(b"\xff\xfe\x00\x01INVALID:\t\x00\xff")

        result = scan_all_runs()
        assert "parse_errors" in result
        assert isinstance(result["parse_errors"], list)
        assert result["runs_scanned"] == 0

    def test_parse_error_listed_with_valid_and_corrupt_runs(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-16 extended: corrupt run alongside a valid run — valid run still returned."""
        monkeypatch.setattr(analytics_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(analytics_mod._config, "runs_root", ".trw/runs")
        monkeypatch.setattr(analytics_mod, "resolve_trw_dir", lambda: tmp_path / ".trw")

        _write_run(
            writer,
            tmp_path,
            "good-task",
            "20260101T000000Z-good0000",
            events=[{"event": "session_start"}],
        )

        corrupt_run_dir = tmp_path / ".trw" / "runs" / "bad-task" / "20260102T000000Z-bad00001" / "meta"
        corrupt_run_dir.mkdir(parents=True)
        (corrupt_run_dir / "run.yaml").write_bytes(b"\xff\xfe INVALID\x00")

        result = scan_all_runs()
        assert result["runs_scanned"] >= 1
        run_ids = [str(r["run_id"]) for r in result["runs"]]
        assert any("good0000" in rid for rid in run_ids)

    def test_ceremony_trend_sorted_ascending(self, multi_run_project: Path) -> None:
        """T-17: ceremony_trend is sorted by started_at ascending."""
        result = scan_all_runs()
        trend = result["aggregate"]["ceremony_trend"]  # type: ignore[index]
        assert isinstance(trend, list)
        assert len(trend) == 3

        started_ats = [str(entry["started_at"]) for entry in trend]
        assert started_ats == sorted(started_ats), f"ceremony_trend not sorted ascending: {started_ats}"

    def test_future_date_filter_returns_empty(self, multi_run_project: Path) -> None:
        """T-24: since filter with future date returns empty results."""
        result = scan_all_runs(since="2099-01-01")
        assert result["runs_scanned"] == 0
        assert result["runs"] == []

    def test_empty_task_root_returns_empty_report(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """scan_all_runs returns empty report when runs_root directory does not exist."""
        monkeypatch.setattr(analytics_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(analytics_mod._config, "runs_root", ".trw/runs")

        result = scan_all_runs()
        assert result["runs_scanned"] == 0
        assert result["runs"] == []
        assert result["parse_errors"] == []

    def test_aggregate_avg_ceremony_score(self, multi_run_project: Path) -> None:
        """Aggregate avg_ceremony_score is computed correctly across all runs."""
        result = scan_all_runs()
        aggregate = result["aggregate"]
        expected_avg = round((45 + 70 + 25) / 3, 2)
        assert aggregate["avg_ceremony_score"] == expected_avg  # type: ignore[index]

    def test_aggregate_build_pass_rate(self, multi_run_project: Path) -> None:
        """Aggregate build_pass_rate is 1.0 when only one build run passed."""
        result = scan_all_runs()
        aggregate = result["aggregate"]
        assert aggregate["build_pass_rate"] == 1.0  # type: ignore[index]

    def test_aggregate_total_runs(self, multi_run_project: Path) -> None:
        """Aggregate total_runs matches runs_scanned."""
        result = scan_all_runs()
        assert result["aggregate"]["total_runs"] == result["runs_scanned"]  # type: ignore[index]

    def test_runs_sorted_ascending_by_started_at(self, multi_run_project: Path) -> None:
        """Returned runs list is sorted by started_at ascending."""
        result = scan_all_runs()
        runs = result["runs"]
        started_ats = [str(r["started_at"]) for r in runs]
        assert started_ats == sorted(started_ats)

    def test_per_run_fields_present(self, multi_run_project: Path) -> None:
        """Each run entry contains required fields."""
        result = scan_all_runs()
        required_fields = {
            "run_id",
            "started_at",
            "task",
            "status",
            "phase",
            "score",
            "session_start",
            "deliver",
            "checkpoint_count",
            "learn_count",
            "build_check",
            "build_passed",
        }
        for run in result["runs"]:
            missing = required_fields - set(run.keys())
            assert not missing, f"Run {run.get('run_id')} missing fields: {missing}"

    def test_task_dir_without_runs_subdir_skipped(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Task directory without a 'runs' subdirectory is silently skipped."""
        monkeypatch.setattr(analytics_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(analytics_mod._config, "runs_root", ".trw/runs")
        monkeypatch.setattr(analytics_mod, "resolve_trw_dir", lambda: tmp_path / ".trw")

        (tmp_path / ".trw" / "runs" / "orphan-task").mkdir(parents=True)

        result = scan_all_runs()
        assert result["runs_scanned"] == 0

    def test_no_events_file_run_still_scanned(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A run with missing events.jsonl is still scanned (score 0)."""
        monkeypatch.setattr(analytics_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(analytics_mod._config, "runs_root", ".trw/runs")
        monkeypatch.setattr(analytics_mod, "resolve_trw_dir", lambda: tmp_path / ".trw")

        _write_run(writer, tmp_path, "silent-task", "20260101T000000Z-silent00", events=None)

        result = scan_all_runs()
        assert result["runs_scanned"] == 1
        run = result["runs"][0]
        assert run["score"] == 0
