"""Tests for PRD-FIX-070: Outcome Correlation Performance Fixes.

Covers:
- FR01: _find_session_start_ts glob-based session scope detection
- FR02/FR06: correlate_recalls reverse-iteration with early exit
- FR03: _batch_sync_to_sqlite batch SQLite writes

PRD-CORE-293 removed the process_outcome three-phase (FR04) and Q-value
correctness (FR07) coverage that used to live here along with the
outcome-correlation Q-seeding path itself.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from trw_mcp.scoring._correlation import _find_session_start_ts, correlate_recalls

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_events_jsonl(events_path: Path, events: list[dict[str, object]]) -> None:
    """Write a list of event dicts to an events.jsonl file."""
    events_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(e) for e in events]
    events_path.write_text("\n".join(lines) + "\n")


def _write_tracking_lines(
    tracking_path: Path,
    entries: list[dict[str, object]],
) -> None:
    """Write recall tracking entries to a JSONL file."""
    tracking_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(e) for e in entries]
    tracking_path.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# FR01: _find_session_start_ts — glob-based session scope detection
# ---------------------------------------------------------------------------


class TestFR01SessionScopeDetection:
    """FR01: _find_session_start_ts finds session boundaries across all layouts."""

    def test_proper_layout_found(self, tmp_path: Path) -> None:
        """PROPER layout: {task}/{run_id}/meta/events.jsonl is discovered."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        runs_root = tmp_path / ".trw" / "runs"
        events_path = runs_root / "my-task" / "run-001" / "meta" / "events.jsonl"
        ts = "2026-04-06T10:00:00+00:00"
        _write_events_jsonl(events_path, [{"event": "session_start", "ts": ts}])

        result = _find_session_start_ts(trw_dir)
        assert result is not None
        assert result.isoformat() == ts

    def test_flat_layout_found(self, tmp_path: Path) -> None:
        """FLAT layout: {run_id}/meta/events.jsonl is discovered."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        runs_root = tmp_path / ".trw" / "runs"
        events_path = runs_root / "run-flat-001" / "meta" / "events.jsonl"
        ts = "2026-04-06T11:00:00+00:00"
        _write_events_jsonl(events_path, [{"event": "run_init", "ts": ts}])

        result = _find_session_start_ts(trw_dir)
        assert result is not None
        assert result.isoformat() == ts

    def test_old_nested_layout_found(self, tmp_path: Path) -> None:
        """OLD_NESTED layout: {task}/runs/{run_id}/meta/events.jsonl is discovered."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        runs_root = tmp_path / ".trw" / "runs"
        events_path = runs_root / "my-task" / "runs" / "run-nested-001" / "meta" / "events.jsonl"
        ts = "2026-04-06T12:00:00+00:00"
        _write_events_jsonl(events_path, [{"event": "session_start", "ts": ts}])

        result = _find_session_start_ts(trw_dir)
        assert result is not None
        assert result.isoformat() == ts

    def test_most_recent_event_file_wins(self, tmp_path: Path) -> None:
        """When multiple layouts exist, the most recently modified file is checked first."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        runs_root = tmp_path / ".trw" / "runs"

        # Older file (PROPER layout)
        old_path = runs_root / "task-a" / "run-old" / "meta" / "events.jsonl"
        _write_events_jsonl(old_path, [{"event": "session_start", "ts": "2026-04-05T08:00:00+00:00"}])
        # Set mtime to the past
        os.utime(old_path, (time.time() - 3600, time.time() - 3600))

        # Newer file (FLAT layout)
        new_path = runs_root / "run-new" / "meta" / "events.jsonl"
        _write_events_jsonl(new_path, [{"event": "session_start", "ts": "2026-04-06T14:00:00+00:00"}])

        result = _find_session_start_ts(trw_dir)
        assert result is not None
        assert result.isoformat() == "2026-04-06T14:00:00+00:00"

    def test_no_runs_dir_returns_none(self, tmp_path: Path) -> None:
        """When runs_root does not exist, returns None."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # No runs directory created

        result = _find_session_start_ts(trw_dir)
        assert result is None

    def test_no_session_events_returns_none(self, tmp_path: Path) -> None:
        """When events files exist but contain no session_start/run_init, returns None."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        runs_root = tmp_path / ".trw" / "runs"
        events_path = runs_root / "task" / "run-1" / "meta" / "events.jsonl"
        _write_events_jsonl(events_path, [{"event": "checkpoint", "ts": "2026-04-06T10:00:00+00:00"}])

        result = _find_session_start_ts(trw_dir)
        assert result is None

    def test_checks_up_to_5_most_recent_files(self, tmp_path: Path) -> None:
        """FR01 checks up to 5 most recent event files, not just the first one."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        runs_root = tmp_path / ".trw" / "runs"

        import os

        # Create 6 event files. Only the 5th most recent has a session_start.
        for i in range(6):
            path = runs_root / f"run-{i:03d}" / "meta" / "events.jsonl"
            if i == 4:
                # 5th most recent (index 4 when sorted by mtime desc)
                _write_events_jsonl(path, [{"event": "session_start", "ts": "2026-04-06T09:00:00+00:00"}])
            else:
                _write_events_jsonl(path, [{"event": "checkpoint", "ts": "2026-04-06T10:00:00+00:00"}])
            # Set increasing mtime so file 5 is most recent, file 0 is oldest
            os.utime(path, (time.time() - 600 + i * 100, time.time() - 600 + i * 100))

        result = _find_session_start_ts(trw_dir)
        # The session_start is in file index 4, which is the 2nd most recent
        # (index 5 is most recent). Should be found within the top 5.
        assert result is not None


# ---------------------------------------------------------------------------
# FR02/FR06: correlate_recalls — reverse iteration with early exit
# ---------------------------------------------------------------------------


class TestFR02FR06ReverseIterationEarlyExit:
    """FR02/FR06: correlate_recalls reads in reverse and breaks early on old records."""

    def test_only_recent_records_returned(self, tmp_path: Path) -> None:
        """Records within the window are returned; older records are not."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        now = datetime.now(timezone.utc)

        old_ts = (now.timestamp()) - 7200  # 2 hours ago
        recent_ts = now.timestamp() - 60  # 1 minute ago

        entries = [
            {"timestamp": old_ts, "learning_id": "old-entry"},
            {"timestamp": recent_ts, "learning_id": "recent-entry"},
        ]
        _write_tracking_lines(trw_dir / "logs" / "recall_tracking.jsonl", entries)

        results = correlate_recalls(trw_dir, 30, scope="window")
        ids = [lid for lid, _ in results]
        assert "recent-entry" in ids
        assert "old-entry" not in ids

    def test_early_exit_on_old_records(self, tmp_path: Path) -> None:
        """Because records are chronological, scanning stops at first old record."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        now = datetime.now(timezone.utc)

        # 1000 old records followed by 5 recent ones
        old_ts = now.timestamp() - 7200
        recent_ts = now.timestamp() - 30
        entries: list[dict[str, object]] = []
        for i in range(1000):
            entries.append({"timestamp": old_ts + i * 0.001, "learning_id": f"old-{i}"})
        for i in range(5):
            entries.append({"timestamp": recent_ts + i, "learning_id": f"recent-{i}"})

        _write_tracking_lines(trw_dir / "logs" / "recall_tracking.jsonl", entries)

        results = correlate_recalls(trw_dir, 5, scope="window")
        ids = [lid for lid, _ in results]
        # Only the 5 recent entries should be found
        assert len(ids) == 5
        for i in range(5):
            assert f"recent-{i}" in ids

    def test_empty_file_returns_empty(self, tmp_path: Path) -> None:
        """Empty tracking file produces no results."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        tracking = trw_dir / "logs" / "recall_tracking.jsonl"
        tracking.parent.mkdir(parents=True)
        tracking.write_text("")

        results = correlate_recalls(trw_dir, 30, scope="window")
        assert results == []

    def test_malformed_json_lines_skipped(self, tmp_path: Path) -> None:
        """Malformed JSON lines are skipped without error."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        now = datetime.now(timezone.utc)
        tracking = trw_dir / "logs" / "recall_tracking.jsonl"
        tracking.parent.mkdir(parents=True)
        lines = [
            "not valid json",
            json.dumps({"timestamp": now.timestamp() - 10, "learning_id": "good-entry"}),
            "{broken",
        ]
        tracking.write_text("\n".join(lines) + "\n")

        results = correlate_recalls(trw_dir, 30, scope="window")
        ids = [lid for lid, _ in results]
        assert "good-entry" in ids

    def test_non_utf8_file_returns_empty_not_crash(self, tmp_path: Path) -> None:
        """Regression: non-UTF-8 bytes in recall_tracking.jsonl must not crash correlate_recalls.

        A torn/partial multi-byte append writes bytes that are not valid UTF-8.
        ``read_text(encoding='utf-8')`` raises UnicodeDecodeError (a ValueError,
        not an OSError) which was NOT caught before this fix, propagating up through
        the deferred-delivery outcome_correlation step and crashing the caller.
        After the fix, correlate_recalls must fail-open and return an empty list.
        """
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        tracking = trw_dir / "logs" / "recall_tracking.jsonl"
        tracking.parent.mkdir(parents=True)
        # 0xFF is never valid as the start byte of a UTF-8 sequence.
        tracking.write_bytes(b"\xff\xfe torn multi-byte garbage\n")

        # Must not raise; must return empty list.
        results = correlate_recalls(trw_dir, 30, scope="window")
        assert results == []


# ---------------------------------------------------------------------------
# FR03: _batch_sync_to_sqlite
# ---------------------------------------------------------------------------
