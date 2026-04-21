"""PRD-CORE-144 FR02: session-scoped sliding window for recall pull rate."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state.surface_tracking import (
    compute_recall_pull_rate,
    log_surface_event,
)


class TestScopedPullRate:
    def test_filters_events_by_session_id(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # Session A: 2 nudges, 1 recall
        log_surface_event(trw_dir, learning_id="A-1", surface_type="nudge", session_id="sess-A")
        log_surface_event(trw_dir, learning_id="A-2", surface_type="nudge", session_id="sess-A")
        log_surface_event(trw_dir, learning_id="A-1", surface_type="recall", session_id="sess-A")
        # Session B: 4 nudges, 0 recalls
        for i in range(4):
            log_surface_event(
                trw_dir, learning_id=f"B-{i}", surface_type="nudge", session_id="sess-B"
            )

        rate_a, count_a, ids_a = compute_recall_pull_rate(trw_dir, session_id="sess-A")
        assert count_a == 2
        assert rate_a == 0.5
        assert set(ids_a) == {"A-1", "A-2"}

        rate_b, count_b, ids_b = compute_recall_pull_rate(trw_dir, session_id="sess-B")
        assert count_b == 4
        assert rate_b == 0.0
        assert set(ids_b) == {"B-0", "B-1", "B-2", "B-3"}

    def test_unknown_session_returns_zero(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_surface_event(trw_dir, learning_id="L-1", surface_type="nudge", session_id="sess-A")
        rate, count, ids = compute_recall_pull_rate(trw_dir, session_id="other")
        assert (rate, count, ids) == (0.0, 0, [])

    def test_missing_session_id_falls_back_to_legacy(self, tmp_path: Path) -> None:
        """session_id=None preserves the legacy unscoped last-500-lines behavior."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_surface_event(trw_dir, learning_id="L-1", surface_type="nudge", session_id="")
        log_surface_event(trw_dir, learning_id="L-1", surface_type="recall", session_id="")
        rate, count, ids = compute_recall_pull_rate(trw_dir)  # no session_id kwarg
        assert rate == 1.0
        assert count == 1
        assert ids == ["L-1"]

    def test_empty_log_returns_zero_triple(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # scoped
        assert compute_recall_pull_rate(trw_dir, session_id="x") == (0.0, 0, [])
        # unscoped
        assert compute_recall_pull_rate(trw_dir) == (0.0, 0, [])

    def test_scoped_read_beats_500_line_tail(self, tmp_path: Path) -> None:
        """A session's events deep in the log must still be countable via scoped read."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # Write 600 noise events with an empty session_id (> 500 line tail cap)
        for i in range(600):
            log_surface_event(
                trw_dir, learning_id=f"noise-{i}", surface_type="recall", session_id=""
            )
        # Then 2 session-scoped events for session "live"
        log_surface_event(trw_dir, learning_id="target", surface_type="nudge", session_id="live")
        log_surface_event(trw_dir, learning_id="target", surface_type="recall", session_id="live")

        rate, count, ids = compute_recall_pull_rate(trw_dir, session_id="live")
        assert rate == 1.0
        assert count == 1
        assert ids == ["target"]
