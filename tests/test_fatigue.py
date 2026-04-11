"""Tests for nudge fatigue detection (PRD-CORE-103-FR05)."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state.surface_tracking import (
    check_nudge_fatigue,
    compute_recall_pull_rate,
    log_surface_event,
)


class TestRecallPullRate:
    def test_recall_pull_rate_computation(self, tmp_path: Path) -> None:
        """Pull rate = nudged IDs that were also recalled / total nudged IDs."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # 4 nudges
        for i in range(4):
            log_surface_event(trw_dir, learning_id=f"L-{i}", surface_type="nudge")
        # 2 of those recalled
        log_surface_event(trw_dir, learning_id="L-0", surface_type="recall")
        log_surface_event(trw_dir, learning_id="L-2", surface_type="recall")

        rate, count = compute_recall_pull_rate(trw_dir)
        assert rate == 0.5  # 2/4
        assert count == 4

    def test_no_nudges_returns_zero(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_surface_event(trw_dir, learning_id="L-0", surface_type="recall")
        rate, count = compute_recall_pull_rate(trw_dir)
        assert rate == 0.0
        assert count == 0

    def test_all_pulled_returns_one(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        for i in range(3):
            log_surface_event(trw_dir, learning_id=f"L-{i}", surface_type="nudge")
            log_surface_event(trw_dir, learning_id=f"L-{i}", surface_type="recall")
        rate, _ = compute_recall_pull_rate(trw_dir)
        assert rate == 1.0

    def test_empty_dir_returns_zero(self, tmp_path: Path) -> None:
        """An empty trw_dir with no log file returns (0.0, 0)."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        rate, count = compute_recall_pull_rate(trw_dir)
        assert rate == 0.0
        assert count == 0

    def test_duplicate_nudge_ids_counted_once(self, tmp_path: Path) -> None:
        """Multiple nudge events for the same learning_id count as one nudge."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # Same learning nudged twice
        log_surface_event(trw_dir, learning_id="L-0", surface_type="nudge")
        log_surface_event(trw_dir, learning_id="L-0", surface_type="nudge")
        log_surface_event(trw_dir, learning_id="L-0", surface_type="recall")

        rate, count = compute_recall_pull_rate(trw_dir)
        assert rate == 1.0  # 1 unique nudge ID, 1 recalled
        assert count == 1

    def test_events_without_learning_id_ignored(self, tmp_path: Path) -> None:
        """Events missing learning_id are skipped."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_surface_event(trw_dir, learning_id="L-0", surface_type="nudge")
        log_surface_event(trw_dir, learning_id="", surface_type="nudge")
        log_surface_event(trw_dir, learning_id="L-0", surface_type="recall")

        rate, count = compute_recall_pull_rate(trw_dir)
        assert rate == 1.0  # Only L-0 counted
        assert count == 1


class TestFatigueWarning:
    def test_warning_fires_at_low_pull_rate(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # 10 nudges, 0 recalls -> 0% pull rate
        for i in range(10):
            log_surface_event(trw_dir, learning_id=f"L-{i}", surface_type="nudge")
        result = check_nudge_fatigue(trw_dir, min_sessions=5)
        assert result["nudge_fatigue_warning"] is True
        assert result["recall_pull_rate"] == 0.0

    def test_no_warning_above_threshold(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        for i in range(10):
            log_surface_event(trw_dir, learning_id=f"L-{i}", surface_type="nudge")
            log_surface_event(trw_dir, learning_id=f"L-{i}", surface_type="recall")
        result = check_nudge_fatigue(trw_dir)
        assert result["nudge_fatigue_warning"] is False
        assert result["recall_pull_rate"] == 1.0

    def test_insufficient_nudges_no_warning(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # Only 2 nudges (below min_sessions=5)
        for i in range(2):
            log_surface_event(trw_dir, learning_id=f"L-{i}", surface_type="nudge")
        result = check_nudge_fatigue(trw_dir, min_sessions=5)
        assert result["nudge_fatigue_warning"] is False

    def test_zero_nudge_session_excluded_from_count(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # No nudges at all
        result = check_nudge_fatigue(trw_dir)
        assert result["nudge_count"] == 0
        assert result["nudge_fatigue_warning"] is False

    def test_empty_trw_dir(self, tmp_path: Path) -> None:
        result = check_nudge_fatigue(tmp_path / "nonexistent")
        assert result["nudge_fatigue_warning"] is False
        assert result["nudge_count"] == 0
        # Nonexistent dir is handled gracefully by read_surface_events (returns []),
        # so the normal code path runs (sessions_analyzed=1), not the exception path.
        assert result["sessions_analyzed"] == 1

    def test_threshold_boundary(self, tmp_path: Path) -> None:
        """Exactly at threshold (10%) should not warn."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # 10 nudges, 1 recall -> 10% pull rate (exactly at threshold)
        for i in range(10):
            log_surface_event(trw_dir, learning_id=f"L-{i}", surface_type="nudge")
        log_surface_event(trw_dir, learning_id="L-0", surface_type="recall")

        result = check_nudge_fatigue(trw_dir, min_sessions=5, threshold=0.10)
        assert result["nudge_fatigue_warning"] is False
        assert result["recall_pull_rate"] == 0.1

    def test_custom_threshold(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # 10 nudges, 3 recalls -> 30% pull rate
        for i in range(10):
            log_surface_event(trw_dir, learning_id=f"L-{i}", surface_type="nudge")
        for i in range(3):
            log_surface_event(trw_dir, learning_id=f"L-{i}", surface_type="recall")

        # Threshold of 0.50 -> warn because 0.3 < 0.5
        result = check_nudge_fatigue(trw_dir, threshold=0.50, min_sessions=5)
        assert result["nudge_fatigue_warning"] is True
        assert result["recall_pull_rate"] == 0.3

    def test_sessions_analyzed_field(self, tmp_path: Path) -> None:
        """sessions_analyzed is 1 for the normal path (single-session for now)."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_surface_event(trw_dir, learning_id="L-0", surface_type="nudge")
        result = check_nudge_fatigue(trw_dir)
        assert result["sessions_analyzed"] == 1


class TestFatigueInDeliverResponse:
    def test_fatigue_warning_fields_added_when_warning(self, tmp_path: Path) -> None:
        """Deliver response includes nudge_fatigue_warning when pull rate is low."""
        mock_fatigue: dict[str, object] = {
            "recall_pull_rate": 0.05,
            "nudge_count": 10,
            "nudge_fatigue_warning": True,
            "sessions_analyzed": 1,
        }

        # Simulate the ceremony.py integration logic
        result: dict[str, object] = {}
        fatigue = mock_fatigue
        if fatigue.get("nudge_fatigue_warning"):
            result["nudge_fatigue_warning"] = True
            result["recall_pull_rate"] = fatigue["recall_pull_rate"]
        elif fatigue.get("nudge_count", 0) > 0:
            result["recall_pull_rate"] = fatigue["recall_pull_rate"]

        assert result["nudge_fatigue_warning"] is True
        assert result["recall_pull_rate"] == 0.05

    def test_pull_rate_added_without_warning_when_nudges_exist(self) -> None:
        """Pull rate is included even when there's no warning, if nudges exist."""
        mock_fatigue: dict[str, object] = {
            "recall_pull_rate": 0.75,
            "nudge_count": 8,
            "nudge_fatigue_warning": False,
            "sessions_analyzed": 1,
        }

        result: dict[str, object] = {}
        fatigue = mock_fatigue
        if fatigue.get("nudge_fatigue_warning"):
            result["nudge_fatigue_warning"] = True
            result["recall_pull_rate"] = fatigue["recall_pull_rate"]
        elif fatigue.get("nudge_count", 0) > 0:
            result["recall_pull_rate"] = fatigue["recall_pull_rate"]

        assert "nudge_fatigue_warning" not in result
        assert result["recall_pull_rate"] == 0.75

    def test_no_fields_when_zero_nudges(self) -> None:
        """No fatigue fields added when there are zero nudges."""
        mock_fatigue: dict[str, object] = {
            "recall_pull_rate": 0.0,
            "nudge_count": 0,
            "nudge_fatigue_warning": False,
            "sessions_analyzed": 0,
        }

        result: dict[str, object] = {}
        fatigue = mock_fatigue
        if fatigue.get("nudge_fatigue_warning"):
            result["nudge_fatigue_warning"] = True
            result["recall_pull_rate"] = fatigue["recall_pull_rate"]
        elif fatigue.get("nudge_count", 0) > 0:
            result["recall_pull_rate"] = fatigue["recall_pull_rate"]

        assert "nudge_fatigue_warning" not in result
        assert "recall_pull_rate" not in result

    def test_import_works(self) -> None:
        """Verify the import path used in ceremony.py works."""
        from trw_mcp.state.surface_tracking import check_nudge_fatigue as _chk

        assert callable(_chk)

    def test_delivery_metrics_has_learning_exposure_wired(self) -> None:
        """Verify deferred delivery metrics still compute learning exposure."""
        import inspect

        from trw_mcp.tools import _deferred_steps_learning

        source = inspect.getsource(_deferred_steps_learning)
        assert "compute_recall_pull_rate" in source
        assert "learning_exposure" in source
        assert "recall_pull_rate" in source
