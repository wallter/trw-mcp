"""Tests for post-run analytics report — PRD-CORE-030.

Covers: RunReport model, event parsing, phase timeline, learning yield,
graceful degradation, and tool integration.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import get_tools_sync
from trw_mcp.models.report import (
    BuildSummary,
    DurationInfo,
    EventSummary,
    LearningSummary,
    PhaseEntry,
    RunReport,
)
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.report import (
    assemble_report,
    compute_learning_yield,
    parse_run_events,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def report_run_dir(tmp_path: Path, writer: FileStateWriter) -> Path:
    """Create a rich run directory with events, checkpoints, and build status."""
    run_dir = tmp_path / "docs" / "analytics-task" / "runs" / "20260219T100000Z-aaaa1111"
    meta = run_dir / "meta"
    meta.mkdir(parents=True)

    # run.yaml
    writer.write_yaml(
        meta / "run.yaml",
        {
            "run_id": "20260219T100000Z-aaaa1111",
            "task": "analytics-task",
            "framework": "v24.0_TRW",
            "status": "complete",
            "phase": "deliver",
            "confidence": "high",
            "run_type": "implementation",
            "prd_scope": ["PRD-CORE-030"],
        },
    )

    # events.jsonl — 3 phase transitions + other events
    events = [
        {"ts": "2026-02-19T10:00:00Z", "event": "run_init", "task": "analytics-task"},
        {"ts": "2026-02-19T10:01:00Z", "event": "phase_enter", "phase": "research"},
        {"ts": "2026-02-19T10:15:00Z", "event": "phase_enter", "phase": "plan"},
        {"ts": "2026-02-19T10:30:00Z", "event": "phase_enter", "phase": "implement"},
        {"ts": "2026-02-19T11:00:00Z", "event": "checkpoint", "message": "mid-impl"},
        {"ts": "2026-02-19T11:30:00Z", "event": "tests_passed"},
        {"ts": "2026-02-19T11:45:00Z", "event": "phase_revert", "from_phase": "implement", "to_phase": "plan"},
        {"ts": "2026-02-19T12:00:00Z", "event": "phase_enter", "phase": "implement"},
        {"ts": "2026-02-19T12:30:00Z", "event": "phase_enter", "phase": "validate"},
        {"ts": "2026-02-19T13:00:00Z", "event": "phase_enter", "phase": "deliver"},
    ]
    for evt in events:
        writer.append_jsonl(meta / "events.jsonl", evt)

    # checkpoints.jsonl — 2 checkpoints
    writer.append_jsonl(meta / "checkpoints.jsonl", {"ts": "2026-02-19T11:00:00Z", "message": "mid"})
    writer.append_jsonl(meta / "checkpoints.jsonl", {"ts": "2026-02-19T12:30:00Z", "message": "val"})

    # build-status.yaml in .trw/context/
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    writer.write_yaml(
        trw_dir / "context" / "build-status.yaml",
        {
            "tests_passed": True,
            "mypy_clean": True,
            "coverage_pct": 92.5,
            "test_count": 45,
            "duration_secs": 12.3,
        },
    )

    return run_dir


@pytest.fixture
def minimal_run_dir(tmp_path: Path, writer: FileStateWriter) -> Path:
    """Create a minimal run directory with only run.yaml (no events, no build)."""
    run_dir = tmp_path / "docs" / "minimal" / "runs" / "20260219T080000Z-bbbb2222"
    meta = run_dir / "meta"
    meta.mkdir(parents=True)

    writer.write_yaml(
        meta / "run.yaml",
        {
            "run_id": "20260219T080000Z-bbbb2222",
            "task": "minimal",
            "framework": "v24.0_TRW",
            "status": "active",
            "phase": "research",
            "confidence": "medium",
        },
    )

    return run_dir


# ---------------------------------------------------------------------------
# Model Tests (FR01)
# ---------------------------------------------------------------------------


class TestRunReportModel:
    """Tests for RunReport Pydantic v2 model."""

    def test_full_instantiation(self) -> None:
        """RunReport with all fields populated validates successfully."""
        report = RunReport(
            run_id="test-123",
            task="test-task",
            status="complete",
            phase="deliver",
            framework="v24.0_TRW",
            run_type="implementation",
            generated_at="2026-02-19T10:00:00Z",
            prd_scope=["PRD-CORE-030"],
            duration=DurationInfo(
                start_ts="2026-02-19T10:00:00Z",
                end_ts="2026-02-19T13:00:00Z",
                elapsed_seconds=10800.0,
            ),
            phase_timeline=[
                PhaseEntry(
                    phase="research",
                    entered_at="2026-02-19T10:00:00Z",
                    exited_at="2026-02-19T10:15:00Z",
                    duration_seconds=900.0,
                ),
            ],
            event_summary=EventSummary(total_count=5, by_type={"run_init": 1, "phase_enter": 4}),
            checkpoint_count=2,
            learning_summary=LearningSummary(
                total_produced=3,
                avg_impact=0.7,
                high_impact_count=1,
                tags_used=["test"],
            ),
            build=BuildSummary(
                tests_passed=True,
                mypy_clean=True,
                coverage_pct=90.0,
                test_count=40,
                duration_secs=10.0,
            ),
            reversion_rate=0.1,
        )
        assert report.run_id == "test-123"
        assert report.build is not None
        assert report.build.tests_passed is True

    def test_optional_fields_null(self) -> None:
        """RunReport with optional fields as None/defaults validates."""
        report = RunReport(
            run_id="min-123",
            task="minimal",
            status="active",
            phase="research",
            generated_at="2026-02-19T10:00:00Z",
        )
        assert report.build is None
        assert report.checkpoint_count == 0
        assert report.reversion_rate == 0.0
        assert report.phase_timeline == []
        assert report.prd_scope == []

    def test_model_dump_roundtrip(self) -> None:
        """model_dump() produces serializable dict."""
        report = RunReport(
            run_id="rt-123",
            task="roundtrip",
            status="complete",
            phase="deliver",
            generated_at="2026-02-19T10:00:00Z",
            build=BuildSummary(tests_passed=True),
        )
        dumped = report.model_dump()
        assert isinstance(dumped, dict)
        assert dumped["run_id"] == "rt-123"
        assert dumped["build"]["tests_passed"] is True
        # Validate JSON serializable
        json_str = json.dumps(dumped)
        assert "rt-123" in json_str

    def test_phase_entry_duration(self) -> None:
        """PhaseEntry stores computed duration correctly."""
        entry = PhaseEntry(
            phase="implement",
            entered_at="2026-02-19T10:00:00Z",
            exited_at="2026-02-19T11:30:00Z",
            duration_seconds=5400.0,
        )
        assert entry.duration_seconds == 5400.0
        assert entry.phase == "implement"

    def test_phase_entry_no_exit(self) -> None:
        """PhaseEntry with no exit (active phase) is valid."""
        entry = PhaseEntry(
            phase="deliver",
            entered_at="2026-02-19T13:00:00Z",
        )
        assert entry.exited_at is None
        assert entry.duration_seconds is None


# ---------------------------------------------------------------------------
# Event Parsing Tests (FR02)
# ---------------------------------------------------------------------------


class TestEventParsing:
    """Tests for parse_run_events function."""

    def test_known_events(self) -> None:
        """parse_run_events correctly counts event types."""
        events: list[dict[str, object]] = [
            {"ts": "2026-02-19T10:00:00Z", "event": "run_init"},
            {"ts": "2026-02-19T10:01:00Z", "event": "phase_enter", "phase": "research"},
            {"ts": "2026-02-19T10:15:00Z", "event": "phase_enter", "phase": "plan"},
            {"ts": "2026-02-19T10:30:00Z", "event": "checkpoint"},
        ]
        summary, timeline, duration, rate = parse_run_events(events)

        assert summary.total_count == 4
        assert summary.by_type["run_init"] == 1
        assert summary.by_type["phase_enter"] == 2
        assert summary.by_type["checkpoint"] == 1
        assert len(timeline) == 2
        assert rate == 0.0  # No reversions

    def test_empty_events(self) -> None:
        """parse_run_events handles empty event list."""
        summary, timeline, duration, rate = parse_run_events([])

        assert summary.total_count == 0
        assert summary.by_type == {}
        assert timeline == []
        assert duration.start_ts is None
        assert rate == 0.0

    def test_malformed_event_types(self) -> None:
        """Events with missing type field counted as 'unknown'."""
        events: list[dict[str, object]] = [
            {"ts": "2026-02-19T10:00:00Z"},
            {"ts": "2026-02-19T10:01:00Z", "event": "phase_enter", "phase": "research"},
        ]
        summary, _, _, _ = parse_run_events(events)

        assert summary.total_count == 2
        assert summary.by_type.get("unknown") == 1

    def test_phase_timeline_three_transitions(self) -> None:
        """Phase timeline with 3 phase transitions computes durations."""
        events: list[dict[str, object]] = [
            {"ts": "2026-02-19T10:00:00Z", "event": "phase_enter", "phase": "research"},
            {"ts": "2026-02-19T10:15:00Z", "event": "phase_enter", "phase": "plan"},
            {"ts": "2026-02-19T10:30:00Z", "event": "phase_enter", "phase": "implement"},
        ]
        _, timeline, _, _ = parse_run_events(events)

        assert len(timeline) == 3
        assert timeline[0].phase == "research"
        assert timeline[0].duration_seconds == 900.0  # 15 min
        assert timeline[1].phase == "plan"
        assert timeline[1].duration_seconds == 900.0
        assert timeline[2].phase == "implement"
        assert timeline[2].exited_at is None  # Last phase, no exit

    def test_single_phase_no_exit(self) -> None:
        """Single phase_enter produces one entry with no exit."""
        events: list[dict[str, object]] = [
            {"ts": "2026-02-19T10:00:00Z", "event": "phase_enter", "phase": "research"},
        ]
        _, timeline, _, _ = parse_run_events(events)

        assert len(timeline) == 1
        assert timeline[0].exited_at is None
        assert timeline[0].duration_seconds is None

    def test_reversion_rate_computed(self) -> None:
        """Reversion rate computed correctly."""
        events: list[dict[str, object]] = [
            {"ts": "2026-02-19T10:00:00Z", "event": "phase_enter", "phase": "research"},
            {"ts": "2026-02-19T10:15:00Z", "event": "phase_enter", "phase": "plan"},
            {"ts": "2026-02-19T10:20:00Z", "event": "phase_revert", "from": "plan", "to": "research"},
            {"ts": "2026-02-19T10:25:00Z", "event": "phase_enter", "phase": "plan"},
        ]
        _, _, _, rate = parse_run_events(events)

        # 1 revert / (3 phase_enter + 1 revert) = 0.25
        assert rate == pytest.approx(0.25)

    def test_duration_first_last_event(self) -> None:
        """Duration computed from first and last event timestamps."""
        events: list[dict[str, object]] = [
            {"ts": "2026-02-19T10:00:00Z", "event": "run_init"},
            {"ts": "2026-02-19T13:00:00Z", "event": "checkpoint"},
        ]
        _, _, duration, _ = parse_run_events(events)

        assert duration.start_ts == "2026-02-19T10:00:00Z"
        assert duration.end_ts == "2026-02-19T13:00:00Z"
        assert duration.elapsed_seconds == 10800.0  # 3 hours

    def test_event_classification_covers_all_types(self) -> None:
        """All distinct event types get their own count."""
        events: list[dict[str, object]] = [
            {"ts": "2026-02-19T10:00:00Z", "event": "run_init"},
            {"ts": "2026-02-19T10:01:00Z", "event": "tests_passed"},
            {"ts": "2026-02-19T10:02:00Z", "event": "build_passed"},
            {"ts": "2026-02-19T10:03:00Z", "event": "reflection_completed"},
        ]
        summary, _, _, _ = parse_run_events(events)

        assert len(summary.by_type) == 4
        for event_type in ["run_init", "tests_passed", "build_passed", "reflection_completed"]:
            assert summary.by_type[event_type] == 1


# ---------------------------------------------------------------------------
# Learning Yield Tests (FR03)
# ---------------------------------------------------------------------------


class TestLearningYield:
    """Tests for compute_learning_yield function."""

    def test_mixed_impact_learnings(self, tmp_path: Path) -> None:
        """Learning yield computes correct averages with mixed impacts."""
        mock_entries: list[dict[str, object]] = [
            {
                "id": "L-1",
                "summary": "High",
                "detail": "d",
                "impact": 0.9,
                "tags": ["arch"],
                "created": "2026-02-19",
                "status": "active",
            },
            {
                "id": "L-2",
                "summary": "Med",
                "detail": "d",
                "impact": 0.5,
                "tags": ["testing"],
                "created": "2026-02-19",
                "status": "active",
            },
            {
                "id": "L-3",
                "summary": "Low",
                "detail": "d",
                "impact": 0.2,
                "tags": ["arch", "testing"],
                "created": "2026-02-19",
                "status": "active",
            },
        ]
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        reader = FileStateReader()
        with patch("trw_mcp.state.report.list_active_learnings", return_value=mock_entries):
            result = compute_learning_yield(trw_dir, reader)

        assert result.total_produced == 3
        assert result.avg_impact == pytest.approx(0.533, abs=0.01)
        assert result.high_impact_count == 1  # Only 0.9 >= 0.7
        assert "arch" in result.tags_used
        assert "testing" in result.tags_used

    def test_no_learnings(self, tmp_path: Path) -> None:
        """Learning yield returns zeros when list_active_learnings returns empty."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        reader = FileStateReader()
        with patch("trw_mcp.state.report.list_active_learnings", return_value=[]):
            result = compute_learning_yield(trw_dir, reader)

        assert result.total_produced == 0
        assert result.avg_impact == 0.0
        assert result.high_impact_count == 0
        assert result.tags_used == []

    def test_sqlite_error_returns_empty(self, tmp_path: Path) -> None:
        """Learning yield returns empty summary when SQLite raises."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        reader = FileStateReader()
        with patch(
            "trw_mcp.state.report.list_active_learnings",
            side_effect=RuntimeError("db error"),
        ):
            result = compute_learning_yield(trw_dir, reader)

        assert result.total_produced == 0

    def test_date_range_filter(self, tmp_path: Path) -> None:
        """Learning yield filters by date range when provided."""
        mock_entries: list[dict[str, object]] = [
            {
                "id": "L-in",
                "summary": "In range",
                "detail": "d",
                "impact": 0.8,
                "tags": [],
                "created": "2026-02-19",
                "status": "active",
            },
            {
                "id": "L-out",
                "summary": "Out range",
                "detail": "d",
                "impact": 0.8,
                "tags": [],
                "created": "2026-02-10",
                "status": "active",
            },
        ]
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        reader = FileStateReader()
        with patch("trw_mcp.state.report.list_active_learnings", return_value=mock_entries):
            result = compute_learning_yield(
                trw_dir,
                reader,
                run_start="2026-02-19T10:00:00Z",
                run_end="2026-02-19T13:00:00Z",
            )

        assert result.total_produced == 1  # Only in-range entry


# ---------------------------------------------------------------------------
# Integration Tests (FR04)
# ---------------------------------------------------------------------------


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

        # Events
        assert report.event_summary.total_count == 10
        assert report.event_summary.by_type["phase_enter"] == 6
        assert report.event_summary.by_type["phase_revert"] == 1

        # Phase timeline
        assert len(report.phase_timeline) == 6

        # Duration
        assert report.duration.start_ts == "2026-02-19T10:00:00Z"
        assert report.duration.end_ts == "2026-02-19T13:00:00Z"
        assert report.duration.elapsed_seconds == 10800.0

        # Checkpoints
        assert report.checkpoint_count == 2

        # Build
        assert report.build is not None
        assert report.build.tests_passed is True
        assert report.build.mypy_clean is True
        assert report.build.coverage_pct == 92.5
        assert report.build.test_count == 45

        # Reversion rate: 1 revert / (6 phase_enter + 1 revert) = ~0.1429
        assert report.reversion_rate == pytest.approx(0.1429, abs=0.001)

        # Learnings
        assert report.learning_summary.total_produced == 2
        assert report.learning_summary.high_impact_count == 1

        # generated_at is set
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


# ---------------------------------------------------------------------------
# Graceful Degradation Tests (FR05)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tool-layer tests for tools/report.py (L-c28c6287: tool-layer tests mandatory)
# ---------------------------------------------------------------------------


class TestReportToolLayer:
    """Verify trw_run_report and trw_analytics_report are registered and callable."""

    def test_both_tools_registered(self) -> None:
        """Both trw_run_report and trw_analytics_report are discoverable after registration."""
        from fastmcp import FastMCP

        from trw_mcp.tools.report import register_report_tools

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
        from fastmcp import FastMCP

        import trw_mcp.tools.report as report_mod
        from trw_mcp.exceptions import StateError
        from trw_mcp.tools.report import register_report_tools

        def _raise(_: object = None) -> None:
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
        from fastmcp import FastMCP

        import trw_mcp.tools.report as report_mod
        from trw_mcp.state.persistence import FileStateWriter
        from trw_mcp.tools.report import register_report_tools

        writer = FileStateWriter()

        # Build a minimal valid run directory
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

        monkeypatch.setattr(report_mod, "resolve_run_path", lambda _: run_dir)
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
