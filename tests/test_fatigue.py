"""Tests for nudge fatigue detection (PRD-CORE-103-FR05)."""

from __future__ import annotations

import json
from pathlib import Path

from trw_mcp.state.surface_tracking import (
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

        rate, count, _ids = compute_recall_pull_rate(trw_dir)
        assert rate == 0.5  # 2/4
        assert count == 4

    def test_no_nudges_returns_zero(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_surface_event(trw_dir, learning_id="L-0", surface_type="recall")
        rate, count, _ids = compute_recall_pull_rate(trw_dir)
        assert rate == 0.0
        assert count == 0

    def test_all_pulled_returns_one(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        for i in range(3):
            log_surface_event(trw_dir, learning_id=f"L-{i}", surface_type="nudge")
            log_surface_event(trw_dir, learning_id=f"L-{i}", surface_type="recall")
        rate, _count, _ids = compute_recall_pull_rate(trw_dir)
        assert rate == 1.0

    def test_empty_dir_returns_zero(self, tmp_path: Path) -> None:
        """An empty trw_dir with no log file returns (0.0, 0)."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        rate, count, _ids = compute_recall_pull_rate(trw_dir)
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

        rate, count, _ids = compute_recall_pull_rate(trw_dir)
        assert rate == 1.0  # 1 unique nudge ID, 1 recalled
        assert count == 1

    def test_events_without_learning_id_ignored(self, tmp_path: Path) -> None:
        """Events missing learning_id are skipped."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_surface_event(trw_dir, learning_id="L-0", surface_type="nudge")
        log_surface_event(trw_dir, learning_id="", surface_type="nudge")
        log_surface_event(trw_dir, learning_id="L-0", surface_type="recall")

        rate, count, _ids = compute_recall_pull_rate(trw_dir)
        assert rate == 1.0  # Only L-0 counted
        assert count == 1


class TestSessionScopedScanResilience:
    """The session-scoped full-scan path (_read_all_surface_events_for_session)
    must degrade per-line like read_jsonl_tail: one bad byte / non-object row
    is dropped, not the whole scan."""

    @staticmethod
    def _write_log(trw_dir: Path, payload: bytes) -> None:
        log_dir = trw_dir / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "surface_tracking.jsonl").write_bytes(payload)

    def test_non_utf8_row_does_not_discard_session_scan(self, tmp_path: Path) -> None:
        """A non-UTF-8 byte row between valid session events is skipped only."""
        trw_dir = tmp_path / ".trw"
        nudge = json.dumps(
            {"session_id": "S", "learning_id": "L-0", "surface_type": "nudge"},
        ).encode("utf-8")
        recall = json.dumps(
            {"session_id": "S", "learning_id": "L-0", "surface_type": "recall"},
        ).encode("utf-8")
        self._write_log(trw_dir, nudge + b"\n\xff\xfe torn append\n" + recall + b"\n")

        rate, count, _ids = compute_recall_pull_rate(trw_dir, session_id="S")
        # Both valid rows survived the bad byte row: L-0 nudged and recalled.
        assert rate == 1.0
        assert count == 1

    def test_non_object_row_does_not_raise_session_scan(self, tmp_path: Path) -> None:
        """A bare-scalar row no longer AttributeErrors into a wiped scan."""
        trw_dir = tmp_path / ".trw"
        nudge = json.dumps(
            {"session_id": "S", "learning_id": "L-0", "surface_type": "nudge"},
        ).encode("utf-8")
        recall = json.dumps(
            {"session_id": "S", "learning_id": "L-0", "surface_type": "recall"},
        ).encode("utf-8")
        # `42` parses as a valid int but has no .get — previously raised into
        # the broad catch and discarded every event for the session.
        self._write_log(trw_dir, nudge + b"\n42\n" + recall + b"\n")

        rate, count, _ids = compute_recall_pull_rate(trw_dir, session_id="S")
        assert rate == 1.0
        assert count == 1

    def test_all_undecodable_session_scan_returns_zero(self, tmp_path: Path) -> None:
        """An entirely non-UTF-8 log fails open to the neutral (0.0, 0) result."""
        trw_dir = tmp_path / ".trw"
        self._write_log(trw_dir, b"\xff\xfe\n\xfc\xfd\n")
        rate, count, _ids = compute_recall_pull_rate(trw_dir, session_id="S")
        assert rate == 0.0
        assert count == 0


class TestFatigueInDeliverResponse:
    def test_delivery_metrics_has_learning_exposure_wired(self) -> None:
        """Verify deferred delivery metrics still compute learning exposure."""
        import inspect

        from trw_mcp.tools import _deferred_steps_learning

        source = inspect.getsource(_deferred_steps_learning)
        assert "compute_recall_pull_rate" in source
        assert "learning_exposure" in source
        assert "recall_pull_rate" in source
