"""Tests for PRD-FIX-070: Outcome Correlation Performance Fixes.

Covers:
- FR01: _find_session_start_ts glob-based session scope detection
- FR02/FR06: correlate_recalls reverse-iteration with early exit
- FR03: _batch_sync_to_sqlite batch SQLite writes
- FR04: process_outcome three-phase (compute -> YAML -> SQLite) ordering
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_mcp.scoring._correlation import (
    _batch_sync_to_sqlite,
    _find_session_start_ts,
    correlate_recalls,
    process_outcome,
)


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


def _make_sqlite_data(learning_id: str) -> dict[str, object]:
    return {
        "id": learning_id,
        "summary": f"learning {learning_id}",
        "q_value": 0.5,
        "q_observations": 0,
        "recurrence": 1,
        "outcome_history": [],
    }


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
        events_path = (
            runs_root / "my-task" / "runs" / "run-nested-001" / "meta" / "events.jsonl"
        )
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


# ---------------------------------------------------------------------------
# FR03: _batch_sync_to_sqlite
# ---------------------------------------------------------------------------


class TestFR03BatchSQLiteSync:
    """FR03: _batch_sync_to_sqlite groups updates into a single backend session."""

    def test_batch_sync_calls_backend_for_each_entry(self, tmp_path: Path) -> None:
        """Each entry in the batch gets a backend.update() call."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        mock_backend = MagicMock()
        updates: list[tuple[str, Path | None, dict[str, object], float, int, list[object]]] = [
            ("id-1", None, {}, 0.6, 1, ["h1"]),
            ("id-2", None, {}, 0.7, 2, ["h2"]),
            ("id-3", None, {}, 0.8, 3, ["h3"]),
        ]

        with patch(
            "trw_mcp.state.memory_adapter.get_backend",
            return_value=mock_backend,
        ):
            _batch_sync_to_sqlite(updates, trw_dir)

        assert mock_backend.update.call_count == 3
        mock_backend.update.assert_any_call(
            "id-1", q_value=0.6, q_observations=1, outcome_history=["h1"]
        )
        mock_backend.update.assert_any_call(
            "id-2", q_value=0.7, q_observations=2, outcome_history=["h2"]
        )

    def test_batch_sync_single_get_backend_call(self, tmp_path: Path) -> None:
        """get_backend is called only once for the entire batch (not N times)."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        mock_backend = MagicMock()
        updates: list[tuple[str, Path | None, dict[str, object], float, int, list[object]]] = [
            ("id-1", None, {}, 0.6, 1, []),
            ("id-2", None, {}, 0.7, 2, []),
        ]

        with patch(
            "trw_mcp.state.memory_adapter.get_backend",
            return_value=mock_backend,
        ) as mock_get:
            _batch_sync_to_sqlite(updates, trw_dir)

        assert mock_get.call_count == 1

    def test_batch_sync_individual_failure_continues(self, tmp_path: Path) -> None:
        """If one entry fails in the batch, others still get synced."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        mock_backend = MagicMock()
        # Second call raises, first and third succeed
        mock_backend.update.side_effect = [None, RuntimeError("db error"), None]
        updates: list[tuple[str, Path | None, dict[str, object], float, int, list[object]]] = [
            ("id-1", None, {}, 0.6, 1, []),
            ("id-2", None, {}, 0.7, 2, []),
            ("id-3", None, {}, 0.8, 3, []),
        ]

        with patch(
            "trw_mcp.state.memory_adapter.get_backend",
            return_value=mock_backend,
        ):
            _batch_sync_to_sqlite(updates, trw_dir)

        assert mock_backend.update.call_count == 3

    def test_batch_sync_empty_updates_noop(self, tmp_path: Path) -> None:
        """Empty updates list does nothing (no get_backend call)."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        with patch(
            "trw_mcp.state.memory_adapter.get_backend",
        ) as mock_get:
            _batch_sync_to_sqlite([], trw_dir)

        mock_get.assert_not_called()

    def test_batch_sync_get_backend_failure_handled(self, tmp_path: Path) -> None:
        """If get_backend itself fails, the entire batch is gracefully skipped."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        updates: list[tuple[str, Path | None, dict[str, object], float, int, list[object]]] = [
            ("id-1", None, {}, 0.6, 1, []),
        ]

        with patch(
            "trw_mcp.state.memory_adapter.get_backend",
            side_effect=RuntimeError("cannot connect"),
        ):
            # Should not raise
            _batch_sync_to_sqlite(updates, trw_dir)


# ---------------------------------------------------------------------------
# FR04: process_outcome three-phase ordering
# ---------------------------------------------------------------------------


class TestFR04ThreePhaseOrdering:
    """FR04: process_outcome separates compute, YAML write, and SQLite sync phases."""

    def test_yaml_writes_before_sqlite_sync(self, tmp_path: Path) -> None:
        """All YAML writes happen before any SQLite syncs."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        # Write two recall tracking entries
        now_ts = datetime.now(timezone.utc).timestamp()
        entries = [
            {"timestamp": now_ts - 5, "learning_id": "lr-A"},
            {"timestamp": now_ts - 3, "learning_id": "lr-B"},
        ]
        _write_tracking_lines(trw_dir / "logs" / "recall_tracking.jsonl", entries)

        call_order: list[str] = []
        original_write_yaml = MagicMock(side_effect=lambda *a, **kw: call_order.append("yaml_write"))
        original_batch_sync = MagicMock(side_effect=lambda *a, **kw: call_order.append("sqlite_batch"))

        def fake_lookup(
            lid: str, trw_dir: Path, entries_dir: Path
        ) -> tuple[Path | None, dict[str, object] | None]:
            return tmp_path / f"{lid}.yaml", _make_sqlite_data(lid)

        with (
            patch("trw_mcp.state.persistence.FileStateWriter.write_yaml", original_write_yaml),
            patch("trw_mcp.scoring._correlation._batch_sync_to_sqlite", original_batch_sync),
        ):
            updated = process_outcome(trw_dir, 0.8, "tests_passed", lookup_fn=fake_lookup)

        assert len(updated) == 2
        # Verify ordering: all yaml writes before sqlite batch
        yaml_indices = [i for i, c in enumerate(call_order) if c == "yaml_write"]
        sqlite_indices = [i for i, c in enumerate(call_order) if c == "sqlite_batch"]
        assert yaml_indices, "YAML writes should have occurred"
        assert sqlite_indices, "SQLite batch sync should have occurred"
        assert max(yaml_indices) < min(sqlite_indices), (
            f"All YAML writes must complete before SQLite batch. Order: {call_order}"
        )

    def test_process_outcome_uses_batch_sync(self, tmp_path: Path) -> None:
        """process_outcome calls _batch_sync_to_sqlite instead of individual _sync_to_sqlite."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        now_ts = datetime.now(timezone.utc).timestamp()
        _write_tracking_lines(
            trw_dir / "logs" / "recall_tracking.jsonl",
            [{"timestamp": now_ts, "learning_id": "lr-1"}],
        )

        def fake_lookup(
            lid: str, trw_dir: Path, entries_dir: Path
        ) -> tuple[Path | None, dict[str, object] | None]:
            return None, _make_sqlite_data(lid)

        with (
            patch("trw_mcp.scoring._correlation._batch_sync_to_sqlite") as mock_batch,
            patch("trw_mcp.scoring._correlation._sync_to_sqlite") as mock_individual,
        ):
            process_outcome(trw_dir, 0.5, "task_complete", lookup_fn=fake_lookup)

        mock_batch.assert_called_once()
        mock_individual.assert_not_called()

    def test_yaml_write_failure_excludes_id_from_updated(self, tmp_path: Path) -> None:
        """If YAML write fails for an entry, its ID is excluded from updated_ids."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        now_ts = datetime.now(timezone.utc).timestamp()
        _write_tracking_lines(
            trw_dir / "logs" / "recall_tracking.jsonl",
            [
                {"timestamp": now_ts, "learning_id": "lr-good"},
                {"timestamp": now_ts - 1, "learning_id": "lr-bad"},
            ],
        )

        call_count = 0

        def failing_write_yaml(path: Path, data: dict[str, object]) -> None:
            nonlocal call_count
            call_count += 1
            if "lr-bad" in str(path):
                raise OSError("disk full")

        def fake_lookup(
            lid: str, trw_dir: Path, entries_dir: Path
        ) -> tuple[Path | None, dict[str, object] | None]:
            return tmp_path / f"{lid}.yaml", _make_sqlite_data(lid)

        with (
            patch("trw_mcp.state.persistence.FileStateWriter.write_yaml", side_effect=failing_write_yaml),
            patch("trw_mcp.scoring._correlation._batch_sync_to_sqlite"),
        ):
            updated = process_outcome(trw_dir, 0.8, "tests_passed", lookup_fn=fake_lookup)

        assert "lr-good" in updated
        assert "lr-bad" not in updated


# ---------------------------------------------------------------------------
# FR07: Q-value correctness regression
# ---------------------------------------------------------------------------


class TestFR07QValueCorrectness:
    """FR07: Q-value updates remain correct after batching optimization."""

    def test_q_value_increases_with_positive_reward(self, tmp_path: Path) -> None:
        """Positive reward should increase Q-value from default 0.5."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        now_ts = datetime.now(timezone.utc).timestamp()
        _write_tracking_lines(
            trw_dir / "logs" / "recall_tracking.jsonl",
            [{"timestamp": now_ts, "learning_id": "q-test-001"}],
        )

        data = _make_sqlite_data("q-test-001")
        original_q = float(str(data["q_value"]))  # 0.5

        def fake_lookup(
            lid: str, trw_dir: Path, entries_dir: Path
        ) -> tuple[Path | None, dict[str, object] | None]:
            return None, data

        with patch("trw_mcp.scoring._correlation._batch_sync_to_sqlite"):
            updated = process_outcome(trw_dir, 0.8, "tests_passed", lookup_fn=fake_lookup)

        assert "q-test-001" in updated
        new_q = float(str(data["q_value"]))
        assert new_q > original_q, f"Positive reward must increase Q-value; {new_q} <= {original_q}"

    def test_q_observations_incremented(self, tmp_path: Path) -> None:
        """q_observations must be incremented after update."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        now_ts = datetime.now(timezone.utc).timestamp()
        _write_tracking_lines(
            trw_dir / "logs" / "recall_tracking.jsonl",
            [{"timestamp": now_ts, "learning_id": "q-obs-001"}],
        )

        data = _make_sqlite_data("q-obs-001")

        def fake_lookup(
            lid: str, trw_dir: Path, entries_dir: Path
        ) -> tuple[Path | None, dict[str, object] | None]:
            return None, data

        with patch("trw_mcp.scoring._correlation._batch_sync_to_sqlite"):
            process_outcome(trw_dir, 0.8, "tests_passed", lookup_fn=fake_lookup)

        assert int(str(data["q_observations"])) == 1

    def test_outcome_history_appended(self, tmp_path: Path) -> None:
        """outcome_history gets a new entry after processing."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        now_ts = datetime.now(timezone.utc).timestamp()
        _write_tracking_lines(
            trw_dir / "logs" / "recall_tracking.jsonl",
            [{"timestamp": now_ts, "learning_id": "hist-001"}],
        )

        data = _make_sqlite_data("hist-001")

        def fake_lookup(
            lid: str, trw_dir: Path, entries_dir: Path
        ) -> tuple[Path | None, dict[str, object] | None]:
            return None, data

        with patch("trw_mcp.scoring._correlation._batch_sync_to_sqlite"):
            process_outcome(trw_dir, 0.8, "tests_passed", lookup_fn=fake_lookup)

        history = data.get("outcome_history")
        assert isinstance(history, list)
        assert len(history) == 1
        assert "tests_passed" in str(history[0])
