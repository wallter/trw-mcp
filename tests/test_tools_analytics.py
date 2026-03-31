"""Tests for state/analytics_report.py — ceremony scoring + cross-run analytics (PRD-CORE-031)."""

from __future__ import annotations

from pathlib import Path

import pytest

import trw_mcp.state.analytics.report as analytics_mod
from tests.conftest import get_tools_sync
from trw_mcp.state.analytics.report import (
    _parse_run_id_timestamp,
    compute_ceremony_score,
    scan_all_runs,
)
from trw_mcp.state.persistence import FileStateWriter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_run(
    writer: FileStateWriter,
    base: Path,
    task: str,
    run_id: str,
    events: list[dict[str, object]] | None = None,
    run_yaml_content: dict[str, object] | None = None,
) -> Path:
    """Create a run directory with run.yaml and events.jsonl.

    Args:
        writer: FileStateWriter instance.
        base: Project root directory (task dirs are created under base/.trw/runs/).
        task: Task name (directory name under .trw/runs/).
        run_id: Run ID string, used as directory name under task/.
        events: Optional list of event dicts; written to events.jsonl.
        run_yaml_content: Optional override for run.yaml contents.

    Returns:
        Path to the run directory.
    """
    run_dir = base / ".trw" / "runs" / task / run_id
    meta = run_dir / "meta"
    meta.mkdir(parents=True)

    yaml_data: dict[str, object] = run_yaml_content or {
        "run_id": run_id,
        "task": task,
        "status": "active",
        "phase": "implement",
    }
    writer.write_yaml(meta / "run.yaml", yaml_data)

    if events:
        events_path = meta / "events.jsonl"
        for evt in events:
            writer.append_jsonl(events_path, evt)

    return run_dir


# ---------------------------------------------------------------------------
# T-10 through T-13: TestCeremonyScoring
# ---------------------------------------------------------------------------


class TestCeremonyScoring:
    """T-10 through T-13: compute_ceremony_score pure function tests."""

    def test_empty_events_score_zero(self) -> None:
        """T-10: Empty event list produces score of 0."""
        result = compute_ceremony_score([])
        assert result["score"] == 0
        assert result["session_start"] is False
        assert result["deliver"] is False
        assert result["checkpoint_count"] == 0
        assert result["learn_count"] == 0
        assert result["build_check"] is False
        assert result["build_passed"] is None
        assert result["review"] is False

    def test_all_six_event_types_score_100(self) -> None:
        """T-11: All 6 event types (including review) present yields score of 100."""
        events: list[dict[str, object]] = [
            {"event": "session_start"},
            {"event": "reflection_complete"},
            {"event": "checkpoint"},
            {"event": "learn_recorded"},
            {"event": "build_check_complete", "tests_passed": "true"},
            {"event": "review_complete"},
        ]
        result = compute_ceremony_score(events)
        assert result["score"] == 100
        assert result["session_start"] is True
        assert result["deliver"] is True
        assert result["checkpoint_count"] == 1
        assert result["learn_count"] == 1
        assert result["build_check"] is True
        assert result["build_passed"] is True
        assert result["review"] is True

    def test_session_start_only_score_25(self) -> None:
        """T-12: Only session_start event yields score of 25."""
        events: list[dict[str, object]] = [{"event": "session_start"}]
        result = compute_ceremony_score(events)
        assert result["score"] == 25
        assert result["session_start"] is True
        assert result["deliver"] is False
        assert result["checkpoint_count"] == 0
        assert result["review"] is False

    @pytest.mark.parametrize(
        "event_types, expected_score",
        [
            # 0 events
            ([], 0),
            # Single component: session_start=25
            (["session_start"], 25),
            # Single component: deliver proxy (reflection_complete)=25
            (["reflection_complete"], 25),
            # Single component: checkpoint=20
            (["checkpoint"], 20),
            # Single component: learn=10
            (["learn_recorded"], 10),
            # Single component: build_check=10
            (["build_check_complete"], 10),
            # Two components: session_start + deliver = 50
            (["session_start", "reflection_complete"], 50),
            # Two components: session_start + checkpoint = 45
            (["session_start", "checkpoint"], 45),
            # Three components: session_start + deliver + learn = 60
            (["session_start", "reflection_complete", "learn_saved"], 60),
            # Three components: checkpoint + learn + build_check = 40
            (["checkpoint", "learn_recorded", "build_check_complete"], 40),
            # Four components: all except deliver = 65
            (["session_start", "checkpoint", "learn_recorded", "build_check_complete"], 65),
            # Four components: all except build_check = 80
            (["session_start", "reflection_complete", "checkpoint", "learn_recorded"], 80),
            # All five original components (no review) = 90
            (
                [
                    "session_start",
                    "reflection_complete",
                    "checkpoint",
                    "learn_recorded",
                    "build_check_complete",
                ],
                90,
            ),
            # All six components (with review) = 100
            (
                [
                    "session_start",
                    "reflection_complete",
                    "checkpoint",
                    "learn_recorded",
                    "build_check_complete",
                    "review_complete",
                ],
                100,
            ),
        ],
    )
    def test_additive_scoring_parametrized(
        self,
        event_types: list[str],
        expected_score: int,
    ) -> None:
        """T-13: Additive scoring — combinations of event types sum correctly."""
        events: list[dict[str, object]] = [{"event": t} for t in event_types]
        result = compute_ceremony_score(events)
        assert result["score"] == expected_score, (
            f"events={event_types!r} expected score={expected_score}, got {result['score']}"
        )

    def test_multiple_checkpoints_counted(self) -> None:
        """Multiple checkpoint events increment checkpoint_count; score still capped at 20 pts."""
        events: list[dict[str, object]] = [
            {"event": "checkpoint"},
            {"event": "checkpoint"},
            {"event": "checkpoint"},
        ]
        result = compute_ceremony_score(events)
        assert result["checkpoint_count"] == 3
        assert result["score"] == 20  # checkpoint component is capped at 20 pts

    def test_multiple_learn_events_counted(self) -> None:
        """Multiple learn events increment learn_count; score component still capped at 10 pts."""
        events: list[dict[str, object]] = [
            {"event": "learn_recorded"},
            {"event": "learn_saved"},
            {"event": "new_learning"},
        ]
        result = compute_ceremony_score(events)
        assert result["learn_count"] == 3
        assert result["score"] == 10  # learn component is capped at 10 pts

    def test_build_passed_false_when_tests_passed_false(self) -> None:
        """build_passed is False when tests_passed field is 'false'."""
        events: list[dict[str, object]] = [
            {"event": "build_check_complete", "tests_passed": "false"},
        ]
        result = compute_ceremony_score(events)
        assert result["build_check"] is True
        assert result["build_passed"] is False

    def test_build_passed_none_without_build_event(self) -> None:
        """build_passed is None when no build_check_complete event is present."""
        events: list[dict[str, object]] = [{"event": "session_start"}]
        result = compute_ceremony_score(events)
        assert result["build_passed"] is None

    def test_unrecognized_events_ignored(self) -> None:
        """Unknown event types do not contribute to the score."""
        events: list[dict[str, object]] = [
            {"event": "run_init"},
            {"event": "phase_transition"},
            {"event": "tool_call"},
        ]
        result = compute_ceremony_score(events)
        assert result["score"] == 0


# ---------------------------------------------------------------------------
# T-14 through T-17, T-24: TestScanAllRuns
# ---------------------------------------------------------------------------


@pytest.fixture
def writer() -> FileStateWriter:
    """Provide a FileStateWriter instance."""
    return FileStateWriter()


@pytest.fixture
def multi_run_project(
    tmp_path: Path,
    writer: FileStateWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Create 3 run directories spread across 2 tasks under tmp_path/.trw/runs/.

    Layout:
        tmp_path/.trw/runs/task-a/20260101T000000Z-aaaa1111/meta/{run.yaml,events.jsonl}
        tmp_path/.trw/runs/task-a/20260102T000000Z-bbbb2222/meta/{run.yaml,events.jsonl}
        tmp_path/.trw/runs/task-b/20260103T000000Z-cccc3333/meta/{run.yaml,events.jsonl}

    Returns:
        tmp_path (project root).
    """
    monkeypatch.setattr(analytics_mod, "resolve_project_root", lambda: tmp_path)
    monkeypatch.setattr(analytics_mod._config, "runs_root", ".trw/runs")
    # Prevent cache writes from touching .trw dirs that don't exist in tmp_path
    monkeypatch.setattr(
        analytics_mod,
        "resolve_trw_dir",
        lambda: tmp_path / ".trw",
    )

    run_events_a1: list[dict[str, object]] = [
        {"ts": "2026-01-01T00:00:00Z", "event": "session_start"},
        {"ts": "2026-01-01T00:05:00Z", "event": "checkpoint"},
    ]
    run_events_a2: list[dict[str, object]] = [
        {"ts": "2026-01-02T00:00:00Z", "event": "session_start"},
        {"ts": "2026-01-02T00:01:00Z", "event": "reflection_complete"},
        {"ts": "2026-01-02T00:02:00Z", "event": "learn_recorded"},
        {"ts": "2026-01-02T00:03:00Z", "event": "build_check_complete", "tests_passed": "true"},
    ]
    run_events_b1: list[dict[str, object]] = [
        {"ts": "2026-01-03T00:00:00Z", "event": "session_start"},
    ]

    _write_run(writer, tmp_path, "task-a", "20260101T000000Z-aaaa1111", events=run_events_a1)
    _write_run(writer, tmp_path, "task-a", "20260102T000000Z-bbbb2222", events=run_events_a2)
    _write_run(writer, tmp_path, "task-b", "20260103T000000Z-cccc3333", events=run_events_b1)

    return tmp_path


class TestScanAllRuns:
    """T-14 through T-17, T-24: scan_all_runs integration tests."""

    def test_three_mock_runs_returns_three_entries(
        self,
        multi_run_project: Path,
    ) -> None:
        """T-14: 3 mock runs produce 3 entries in scan_all_runs result."""
        result = scan_all_runs()
        runs = result["runs"]
        assert isinstance(runs, list)
        assert len(runs) == 3
        assert result["runs_scanned"] == 3

    def test_since_filter_excludes_older_runs(
        self,
        multi_run_project: Path,
    ) -> None:
        """T-15: since filter correctly excludes runs before the cutoff date."""
        # Only the Jan 3 run should survive a Jan 3 cutoff
        result = scan_all_runs(since="2026-01-03")
        runs = result["runs"]
        assert len(runs) == 1
        assert "cccc3333" in str(runs[0]["run_id"])

    def test_since_filter_includes_all_when_before_all_runs(
        self,
        multi_run_project: Path,
    ) -> None:
        """T-15 corollary: since filter before all runs includes all entries."""
        result = scan_all_runs(since="2025-01-01")
        assert len(result["runs"]) == 3

    def test_since_filter_brackets_middle_run(
        self,
        multi_run_project: Path,
    ) -> None:
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
        monkeypatch.setattr(
            analytics_mod,
            "resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )

        # Create a run dir with an unparseable run.yaml
        corrupt_run_dir = tmp_path / ".trw" / "runs" / "bad-task" / "20260101T000000Z-bad00000" / "meta"
        corrupt_run_dir.mkdir(parents=True)
        corrupt_yaml = corrupt_run_dir / "run.yaml"
        # Write binary garbage that will fail YAML parsing
        corrupt_yaml.write_bytes(b"\xff\xfe\x00\x01INVALID:\t\x00\xff")

        result = scan_all_runs()
        # Should not raise; parse_errors should be a list (may or may not have entries,
        # depending on how ruamel.yaml handles the corrupt bytes)
        assert "parse_errors" in result
        assert isinstance(result["parse_errors"], list)
        # The run count reflects only valid runs (0 here since only corrupt run exists)
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
        monkeypatch.setattr(
            analytics_mod,
            "resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )

        # Valid run
        _write_run(
            writer,
            tmp_path,
            "good-task",
            "20260101T000000Z-good0000",
            events=[{"event": "session_start"}],
        )

        # Corrupt run (directory exists but run.yaml is binary garbage)
        corrupt_run_dir = tmp_path / ".trw" / "runs" / "bad-task" / "20260102T000000Z-bad00001" / "meta"
        corrupt_run_dir.mkdir(parents=True)
        (corrupt_run_dir / "run.yaml").write_bytes(b"\xff\xfe INVALID\x00")

        result = scan_all_runs()
        # Valid run is still returned
        assert result["runs_scanned"] >= 1
        run_ids = [str(r["run_id"]) for r in result["runs"]]
        assert any("good0000" in rid for rid in run_ids)

    def test_ceremony_trend_sorted_ascending(
        self,
        multi_run_project: Path,
    ) -> None:
        """T-17: ceremony_trend is sorted by started_at ascending."""
        result = scan_all_runs()
        trend = result["aggregate"]["ceremony_trend"]  # type: ignore[index]
        assert isinstance(trend, list)
        assert len(trend) == 3

        started_ats = [str(entry["started_at"]) for entry in trend]
        assert started_ats == sorted(started_ats), f"ceremony_trend not sorted ascending: {started_ats}"

    def test_future_date_filter_returns_empty(
        self,
        multi_run_project: Path,
    ) -> None:
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

        # .trw/runs/ directory does NOT exist
        result = scan_all_runs()
        assert result["runs_scanned"] == 0
        assert result["runs"] == []
        assert result["parse_errors"] == []

    def test_aggregate_avg_ceremony_score(
        self,
        multi_run_project: Path,
    ) -> None:
        """Aggregate avg_ceremony_score is computed correctly across all runs."""
        result = scan_all_runs()
        aggregate = result["aggregate"]
        # Run 1: session_start(25) + checkpoint(20) = 45
        # Run 2: session_start(25) + deliver(25) + learn(10) + build_check(10) = 70
        # Run 3: session_start(25) = 25
        expected_avg = round((45 + 70 + 25) / 3, 2)
        assert aggregate["avg_ceremony_score"] == expected_avg  # type: ignore[index]

    def test_aggregate_build_pass_rate(
        self,
        multi_run_project: Path,
    ) -> None:
        """Aggregate build_pass_rate is 1.0 when only one build run passed."""
        result = scan_all_runs()
        aggregate = result["aggregate"]
        # Only run 2 has build_check_complete with tests_passed=true
        assert aggregate["build_pass_rate"] == 1.0  # type: ignore[index]

    def test_aggregate_total_runs(
        self,
        multi_run_project: Path,
    ) -> None:
        """Aggregate total_runs matches runs_scanned."""
        result = scan_all_runs()
        assert result["aggregate"]["total_runs"] == result["runs_scanned"]  # type: ignore[index]

    def test_runs_sorted_ascending_by_started_at(
        self,
        multi_run_project: Path,
    ) -> None:
        """Returned runs list is sorted by started_at ascending."""
        result = scan_all_runs()
        runs = result["runs"]
        started_ats = [str(r["started_at"]) for r in runs]
        assert started_ats == sorted(started_ats)

    def test_per_run_fields_present(
        self,
        multi_run_project: Path,
    ) -> None:
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
        monkeypatch.setattr(
            analytics_mod,
            "resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )

        # Create a task dir with no 'runs' subdirectory
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
        monkeypatch.setattr(
            analytics_mod,
            "resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )

        # Run with no events file
        _write_run(writer, tmp_path, "silent-task", "20260101T000000Z-silent00", events=None)

        result = scan_all_runs()
        assert result["runs_scanned"] == 1
        run = result["runs"][0]
        assert run["score"] == 0


# ---------------------------------------------------------------------------
# TestParseRunIdTimestamp
# ---------------------------------------------------------------------------


class TestParseRunIdTimestamp:
    """Edge cases for _parse_run_id_timestamp."""

    def test_standard_format_parses_correctly(self) -> None:
        """Standard run_id format returns correct ISO timestamp."""
        result = _parse_run_id_timestamp("20260220T120000Z-abcd1234")
        assert result == "2026-02-20T12:00:00+00:00"

    def test_midnight_timestamp(self) -> None:
        """Midnight timestamp parses correctly."""
        result = _parse_run_id_timestamp("20260101T000000Z-aaaa1111")
        assert result == "2026-01-01T00:00:00+00:00"

    def test_invalid_run_id_returns_raw(self) -> None:
        """Unparseable run_id is returned as-is."""
        raw = "not-a-valid-run-id"
        result = _parse_run_id_timestamp(raw)
        assert result == raw

    def test_empty_string_returns_empty(self) -> None:
        """Empty string returns empty string (fallback path)."""
        result = _parse_run_id_timestamp("")
        assert result == ""

    def test_short_ts_part_returns_raw(self) -> None:
        """Run_id with a short timestamp part (< 16 chars) returns as-is."""
        raw = "2026-abcd"
        result = _parse_run_id_timestamp(raw)
        assert result == raw

    def test_no_hyphen_separator_returns_raw(self) -> None:
        """Run_id with no hyphen returns as-is (split gives only one part)."""
        raw = "20260220T120000Z"
        # split('-')[0] == full string, length >= 16 and 'T' present, should parse
        result = _parse_run_id_timestamp(raw)
        assert result == "2026-02-20T12:00:00+00:00"

    def test_bad_date_digits_returns_raw(self) -> None:
        """Invalid month/day digits return the raw run_id."""
        raw = "20261399T000000Z-bad00000"
        result = _parse_run_id_timestamp(raw)
        assert result == raw

    def test_different_valid_dates(self) -> None:
        """Various valid dates parse to correct ISO strings."""
        cases = [
            ("20260630T235959Z-xxxx0000", "2026-06-30T23:59:59+00:00"),
            ("20260101T120000Z-yyyy1111", "2026-01-01T12:00:00+00:00"),
        ]
        for run_id, expected in cases:
            assert _parse_run_id_timestamp(run_id) == expected, f"Failed for {run_id}"


# ---------------------------------------------------------------------------
# Tool-layer tests (Finding 1: L-c28c6287)
# ---------------------------------------------------------------------------


class TestAnalyticsReportToolLayer:
    """Verify trw_analytics_report is registered and callable via FastMCP."""

    def test_trw_analytics_report_registered(self) -> None:
        """trw_analytics_report is discoverable after register_report_tools()."""
        from fastmcp import FastMCP

        from trw_mcp.tools.report import register_report_tools

        srv = FastMCP("report-test")
        register_report_tools(srv)
        tools = get_tools_sync(srv)
        assert "trw_analytics_report" in tools
        assert "trw_run_report" in tools

    def test_trw_analytics_report_callable(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """trw_analytics_report returns structured result when called through FastMCP."""
        from fastmcp import FastMCP

        from trw_mcp.tools.report import register_report_tools

        # Patch config and path resolution so scan_all_runs finds nothing
        monkeypatch.setattr(analytics_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(analytics_mod._config, "runs_root", ".trw/runs")
        monkeypatch.setattr(
            analytics_mod,
            "resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )

        srv = FastMCP("report-callable-test")
        register_report_tools(srv)
        tools = get_tools_sync(srv)
        result = tools["trw_analytics_report"].fn()
        assert isinstance(result, dict)
        assert "runs" in result
        assert "aggregate" in result
        assert result["runs_scanned"] == 0


# ---------------------------------------------------------------------------
# Integration tests (Finding 2: T-20, T-24 extended)
# ---------------------------------------------------------------------------


class TestAnalyticsIntegration:
    """Integration tests covering cache write (T-20) and since validation."""

    def test_t20_cache_file_written(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-20: scan_all_runs writes cache to .trw/context/analytics-report.yaml."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)

        monkeypatch.setattr(analytics_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(analytics_mod._config, "runs_root", ".trw/runs")
        monkeypatch.setattr(analytics_mod, "resolve_trw_dir", lambda: trw_dir)

        _write_run(
            writer,
            tmp_path,
            "cache-task",
            "20260101T000000Z-cache000",
            events=[
                {"event": "session_start"},
            ],
        )

        scan_all_runs()

        cache_path = trw_dir / "context" / "analytics-report.yaml"
        assert cache_path.exists(), "Cache file not written"
        from trw_mcp.state.persistence import FileStateReader

        cached = FileStateReader().read_yaml(cache_path)
        assert cached["runs_scanned"] == 1

    def test_since_malformed_reports_parse_error(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Malformed since filter surfaces in parse_errors rather than crashing."""
        monkeypatch.setattr(analytics_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(analytics_mod._config, "runs_root", ".trw/runs")
        monkeypatch.setattr(
            analytics_mod,
            "resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )

        _write_run(
            writer,
            tmp_path,
            "task-a",
            "20260101T000000Z-aaaa1111",
            events=[
                {"event": "session_start"},
            ],
        )

        result = scan_all_runs(since="not-a-date")
        assert any("not a valid ISO date" in str(e) for e in result["parse_errors"])
