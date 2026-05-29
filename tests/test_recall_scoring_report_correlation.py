"""Split scoring correlation coverage tests from test_recall_scoring_report.py."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from tests._recall_scoring_report_support import make_recall_tracking_log, patch_scoring_runs_root
from trw_mcp.state.persistence import FileStateWriter


class TestFindSessionStartTs:
    """Cover _find_session_start_ts function."""

    def test_returns_none_when_task_root_missing(self, tmp_path: Path) -> None:
        """Returns None when task_root directory does not exist (lines 712-713)."""
        from trw_mcp.scoring import _find_session_start_ts

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        result = _find_session_start_ts(trw_dir)
        assert result is None

    def test_finds_run_init_event(self, tmp_path: Path) -> None:
        """Finds run_init event timestamp from events.jsonl (lines 715-738)."""
        from trw_mcp.scoring import _find_session_start_ts

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        meta_dir = tmp_path / "tasks" / "my-task" / "20260101T000000Z-abc12345" / "meta"
        meta_dir.mkdir(parents=True)
        FileStateWriter().write_yaml(meta_dir / "run.yaml", {"id": "20260101T000000Z-abc12345"})
        FileStateWriter().append_jsonl(
            meta_dir / "events.jsonl",
            {
                "ts": "2026-01-01T10:00:00+00:00",
                "event": "run_init",
            },
        )

        with patch_scoring_runs_root():
            result = _find_session_start_ts(trw_dir)
        assert result is not None
        assert result.year == 2026

    def test_finds_session_start_event(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Finds session_start event timestamp (also accepted event type)."""
        from trw_mcp.scoring import _find_session_start_ts

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        meta_dir = tmp_path / "tasks" / "another-task" / "20260115T000000Z-def67890" / "meta"
        meta_dir.mkdir(parents=True)
        writer.write_yaml(meta_dir / "run.yaml", {"id": "20260115T000000Z-def67890"})
        writer.append_jsonl(
            meta_dir / "events.jsonl",
            {
                "ts": "2026-01-15T08:00:00+00:00",
                "event": "session_start",
            },
        )

        with patch_scoring_runs_root():
            result = _find_session_start_ts(trw_dir)
        assert result is not None
        assert result.month == 1
        assert result.day == 15

    def test_invalid_timestamp_in_event_skipped(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Invalid ts in event is skipped without raising (ValueError continue)."""
        from trw_mcp.scoring import _find_session_start_ts

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        meta_dir = tmp_path / "tasks" / "bad-ts-task" / "runs" / "20260101T000000Z-bad12345" / "meta"
        meta_dir.mkdir(parents=True)
        writer.append_jsonl(
            meta_dir / "events.jsonl",
            {
                "ts": "not-a-timestamp",
                "event": "run_init",
            },
        )

        with patch_scoring_runs_root():
            result = _find_session_start_ts(trw_dir)
        assert result is None

    def test_no_matching_events_returns_none(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Non-session events don't update latest_ts, returns None."""
        from trw_mcp.scoring import _find_session_start_ts

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        meta_dir = tmp_path / "tasks" / "no-session-task" / "runs" / "20260101T000000Z-ccc12345" / "meta"
        meta_dir.mkdir(parents=True)
        writer.append_jsonl(
            meta_dir / "events.jsonl",
            {
                "ts": "2026-01-01T10:00:00+00:00",
                "event": "checkpoint",
            },
        )

        with patch_scoring_runs_root():
            result = _find_session_start_ts(trw_dir)
        assert result is None

    def test_task_dir_without_runs_subdir_skipped(self, tmp_path: Path) -> None:
        """Task directory without runs/ subdirectory is skipped gracefully."""
        from trw_mcp.scoring import _find_session_start_ts

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        task_dir = tmp_path / "tasks" / "no-runs"
        task_dir.mkdir(parents=True)
        (task_dir / "somefile.txt").write_text("hello", encoding="utf-8")

        with patch_scoring_runs_root():
            result = _find_session_start_ts(trw_dir)
        assert result is None


class TestFindSessionStartTsRunDirWithoutEvents:
    """Cover events.jsonl missing in a run directory."""

    def test_run_dir_without_events_file_skipped(self, tmp_path: Path) -> None:
        """Run directory without events.jsonl is skipped (line 722 continue)."""
        from trw_mcp.scoring import _find_session_start_ts

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        task_dir = tmp_path / "tasks" / "no-events-task"
        meta_dir = task_dir / "runs" / "20260101T000000Z-noevent1" / "meta"
        meta_dir.mkdir(parents=True)

        with patch_scoring_runs_root():
            result = _find_session_start_ts(trw_dir)
        assert result is None


class TestCorrelateRecalls:
    """Cover correlate_recalls window and validation paths."""

    def test_missing_receipt_log_returns_empty(self, tmp_path: Path) -> None:
        """No receipt log -> empty list (line 773 early return)."""
        from trw_mcp.scoring import correlate_recalls

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        result = correlate_recalls(trw_dir, 30)
        assert result == []

    def test_session_scope_calls_find_session_start(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """scope='session' triggers session-start lookup (line 782 branch)."""
        from trw_mcp.scoring import correlate_recalls

        recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        trw_dir = make_recall_tracking_log(
            tmp_path,
            writer,
            [
                {
                    "ts": recent_ts,
                    "matched_ids": ["L-session"],
                }
            ],
        )

        result = correlate_recalls(trw_dir, 60, scope="session")
        ids = [learning_id for learning_id, _ in result]
        assert "L-session" in ids

    def test_empty_ts_field_is_skipped(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Record with empty ts is skipped (line 792 continue)."""
        from trw_mcp.scoring import correlate_recalls

        trw_dir = make_recall_tracking_log(
            tmp_path,
            writer,
            [
                {"ts": "", "matched_ids": ["L-empty-ts"]},
            ],
        )

        result = correlate_recalls(trw_dir, 30)
        assert result == []

    def test_invalid_ts_format_is_skipped(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Record with invalid ISO ts is skipped via ValueError (lines 795-796)."""
        from trw_mcp.scoring import correlate_recalls

        trw_dir = make_recall_tracking_log(
            tmp_path,
            writer,
            [
                {"ts": "not-a-timestamp", "matched_ids": ["L-bad-ts"]},
            ],
        )

        result = correlate_recalls(trw_dir, 30)
        assert result == []

    def test_receipt_outside_window_skipped(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Receipt older than the window is skipped (receipt_ts < cutoff_ts)."""
        from trw_mcp.scoring import correlate_recalls

        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        trw_dir = make_recall_tracking_log(
            tmp_path,
            writer,
            [
                {"ts": old_ts, "matched_ids": ["L-old"]},
            ],
        )

        result = correlate_recalls(trw_dir, 30)
        assert result == []

    def test_non_string_learning_ids_skipped(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Non-string or empty learning IDs in matched_ids are skipped."""
        from trw_mcp.scoring import correlate_recalls

        now_ts = datetime.now(timezone.utc).isoformat()
        trw_dir = make_recall_tracking_log(
            tmp_path,
            writer,
            [
                {"ts": now_ts, "matched_ids": [None, "", 42, "L-valid"]},
            ],
        )

        result = correlate_recalls(trw_dir, 30)
        ids = [learning_id for learning_id, _ in result]
        assert "L-valid" in ids
        assert None not in ids
        assert "" not in ids

    def test_recent_receipt_produces_discount(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Recent receipt produces a recency discount between floor and 1.0."""
        from trw_mcp.scoring import correlate_recalls

        now_ts = datetime.now(timezone.utc).isoformat()
        trw_dir = make_recall_tracking_log(
            tmp_path,
            writer,
            [
                {"ts": now_ts, "matched_ids": ["L-recent"]},
            ],
        )

        result = correlate_recalls(trw_dir, 30)
        assert len(result) == 1
        learning_id, discount = result[0]
        assert learning_id == "L-recent"
        assert 0.0 < discount <= 1.0


class TestCorrelateRecallsAdvancedPaths:
    """Cover session cutoff override and future timestamp skip."""

    def test_session_scope_with_found_session_start_overrides_cutoff(
        self, tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """scope='session' + found session_start -> cutoff_ts = session_start (line 782)."""
        del monkeypatch
        from trw_mcp.scoring import correlate_recalls

        now = datetime.now(timezone.utc)
        session_start_ts = (now - timedelta(hours=2)).replace(microsecond=0)
        receipt_ts = (now - timedelta(hours=1)).replace(microsecond=0)

        trw_dir = make_recall_tracking_log(
            tmp_path,
            writer,
            [
                {"ts": receipt_ts.isoformat(), "matched_ids": ["L-in-session"]},
            ],
        )

        with (
            patch_scoring_runs_root(),
            patch("trw_mcp.scoring._correlation._find_session_start_ts", return_value=session_start_ts),
        ):
            result = correlate_recalls(trw_dir, 30, scope="session")
        ids = [learning_id for learning_id, _ in result]
        assert "L-in-session" in ids

    def test_future_timestamp_receipt_is_skipped(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Receipt with timestamp in the future has elapsed_secs < 0 -> skipped (line 803)."""
        from trw_mcp.scoring import correlate_recalls

        future_ts = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        trw_dir = tmp_path / ".trw"
        logs_dir = trw_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        writer.append_jsonl(
            logs_dir / "recall_tracking.jsonl",
            {
                "ts": future_ts,
                "matched_ids": ["L-future"],
            },
        )

        result = correlate_recalls(trw_dir, 30)
        ids = [learning_id for learning_id, _ in result]
        assert "L-future" not in ids
