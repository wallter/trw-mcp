"""Run report model tests."""

from __future__ import annotations

import json

from trw_mcp.models.report import (
    BuildSummary,
    DurationInfo,
    EventSummary,
    LearningSummary,
    PhaseEntry,
    RunReport,
)


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
