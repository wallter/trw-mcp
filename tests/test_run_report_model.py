"""Run report model tests."""

from __future__ import annotations

from trw_mcp.models.report import (
    PhaseEntry,
)


class TestRunReportModel:
    """Tests for RunReport Pydantic v2 model."""

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
