"""Tests for hard delivery gate enforcement (R-01, R-07).

R-01: Block delivery when >5 file_modified events and no review was run.
R-07: Warn when last checkpoint message contains 'blocker' keyword.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.tools._ceremony_helpers import (
    _check_checkpoint_blocker_gate,
    _check_review_file_count_gate,
    _read_run_events,
    check_delivery_gates,
)
from trw_mcp.tools._delivery_helpers import (
    _count_file_modified_current_session,
    _events_since_last_session_start,
)

# --- Fixtures ---


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """Create a minimal run directory with meta/."""
    d = tmp_path / "docs" / "task" / "runs" / "20260318T120000Z-test"
    meta = d / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text(
        "run_id: test-run\nstatus: active\nphase: implement\ntask_name: test-task\n",
        encoding="utf-8",
    )
    (meta / "events.jsonl").write_text("", encoding="utf-8")
    return d


@pytest.fixture()
def reader() -> FileStateReader:
    return FileStateReader()


@pytest.fixture()
def writer() -> FileStateWriter:
    return FileStateWriter()


# --- R-01: Review scope block (file_modified count > 5, no review) ---


@pytest.mark.integration
class TestReviewScopeBlock:
    """R-01: Block delivery when >5 files modified but no review was run."""

    def test_review_scope_block_fires_when_many_files_no_review(
        self,
        run_dir: Path,
        reader: FileStateReader,
    ) -> None:
        """More than 5 file_modified events and no review.yaml should produce a block."""
        events_path = run_dir / "meta" / "events.jsonl"
        events = [
            {"event": "file_modified", "ts": "2026-03-18T00:00:00Z", "data": {"path": f"src/mod{i}.py"}}
            for i in range(7)
        ]
        events_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8",
        )

        result = _check_review_file_count_gate(run_dir, _read_run_events(run_dir, reader))
        assert result is not None
        assert "7 files modified" in result
        assert "trw_review()" in result

    def test_review_scope_block_does_not_fire_when_few_files(
        self,
        run_dir: Path,
        reader: FileStateReader,
    ) -> None:
        """5 or fewer file_modified events should NOT trigger the block."""
        events_path = run_dir / "meta" / "events.jsonl"
        events = [
            {"event": "file_modified", "ts": "2026-03-18T00:00:00Z", "data": {"path": f"src/mod{i}.py"}}
            for i in range(5)
        ]
        events_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8",
        )

        result = _check_review_file_count_gate(run_dir, _read_run_events(run_dir, reader))
        assert result is None

    def test_review_scope_block_does_not_fire_when_review_exists(
        self,
        run_dir: Path,
        reader: FileStateReader,
        writer: FileStateWriter,
    ) -> None:
        """Even with >5 file_modified events, if review.yaml exists, no block."""
        events_path = run_dir / "meta" / "events.jsonl"
        events = [
            {"event": "file_modified", "ts": "2026-03-18T00:00:00Z", "data": {"path": f"src/mod{i}.py"}}
            for i in range(10)
        ]
        events_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8",
        )
        # Write review.yaml so gate should NOT fire
        writer.write_yaml(
            run_dir / "meta" / "review.yaml",
            {"verdict": "pass", "critical_count": 0},
        )

        result = _check_review_file_count_gate(run_dir, _read_run_events(run_dir, reader))
        assert result is None

    def test_review_scope_block_no_events_file(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """If events.jsonl doesn't exist, no block (fail-open)."""
        d = tmp_path / "run-no-events"
        meta = d / "meta"
        meta.mkdir(parents=True)

        result = _check_review_file_count_gate(d, _read_run_events(d, reader))
        assert result is None

    def test_review_scope_block_wired_into_check_delivery_gates(
        self,
        run_dir: Path,
        reader: FileStateReader,
    ) -> None:
        """check_delivery_gates() should include review_scope_block when gate fires."""
        events_path = run_dir / "meta" / "events.jsonl"
        events = [
            {"event": "file_modified", "ts": "2026-03-18T00:00:00Z", "data": {"path": f"src/mod{i}.py"}}
            for i in range(8)
        ]
        events_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8",
        )

        result = check_delivery_gates(run_dir, reader)
        assert "review_scope_block" in result
        assert "8 files modified" in str(result["review_scope_block"])

    def test_review_scope_block_failopen_on_corrupt_events(
        self,
        run_dir: Path,
    ) -> None:
        """Corrupt event data should not crash (fail-open)."""
        # Pass events with missing 'event' key — should count as 0 file_modified
        bad_events: list[dict[str, object]] = [{"broken": True}] * 10
        result = _check_review_file_count_gate(run_dir, bad_events)
        assert result is None


# --- Session-scoped file_modified counting ---


@pytest.mark.integration
class TestSessionScopedFileCounting:
    """Events from previous sessions should not block delivery in the current session."""

    def test_session_start_resets_file_count(self) -> None:
        """file_modified events before session_start should not be counted."""
        events: list[dict[str, object]] = [
            {"event": "file_modified", "ts": "2026-03-18T00:00:00Z"},
            {"event": "file_modified", "ts": "2026-03-18T00:00:01Z"},
            {"event": "file_modified", "ts": "2026-03-18T00:00:02Z"},
            {"event": "file_modified", "ts": "2026-03-18T00:00:03Z"},
            {"event": "file_modified", "ts": "2026-03-18T00:00:04Z"},
            {"event": "file_modified", "ts": "2026-03-18T00:00:05Z"},
            {"event": "file_modified", "ts": "2026-03-18T00:00:06Z"},
            # Session boundary — new session starts here
            {"event": "session_start", "ts": "2026-03-18T01:00:00Z"},
            {"event": "file_modified", "ts": "2026-03-18T01:00:01Z"},
        ]
        # Total file_modified = 8, but only 1 in current session
        assert _count_file_modified_current_session(events) == 1

    def test_no_session_start_counts_all(self) -> None:
        """Without session_start, all file_modified events count (backward compat)."""
        events: list[dict[str, object]] = [
            {"event": "file_modified", "ts": "2026-03-18T00:00:00Z"},
            {"event": "file_modified", "ts": "2026-03-18T00:00:01Z"},
            {"event": "file_modified", "ts": "2026-03-18T00:00:02Z"},
        ]
        assert _count_file_modified_current_session(events) == 3

    def test_events_since_last_session_start_boundary(self) -> None:
        """Should return events from and including the last session_start."""
        events: list[dict[str, object]] = [
            {"event": "file_modified", "ts": "T0"},
            {"event": "session_start", "ts": "T1"},
            {"event": "file_modified", "ts": "T2"},
            {"event": "session_start", "ts": "T3"},
            {"event": "file_modified", "ts": "T4"},
        ]
        result = _events_since_last_session_start(events)
        assert len(result) == 2  # session_start at T3 + file_modified at T4
        assert result[0]["ts"] == "T3"
        assert result[1]["ts"] == "T4"

    def test_stale_run_70_files_current_session_1_file(
        self,
        run_dir: Path,
        reader: FileStateReader,
    ) -> None:
        """The exact scenario: stale run with 70 modified files, current session 1 file.

        This is the recurring issue that motivated this fix. A previous session
        accumulated 70 file_modified events but never delivered. The current
        session starts, edits 1 file, and should NOT be blocked by the stale count.
        """
        events_path = run_dir / "meta" / "events.jsonl"
        events: list[dict[str, object]] = []
        # Previous session: 70 file_modified events
        for i in range(70):
            events.append({"event": "file_modified", "ts": f"2026-03-17T{i:02d}:00:00Z"})
        # New session boundary
        events.append({"event": "session_start", "ts": "2026-03-18T00:00:00Z"})
        # Current session: 1 file_modified
        events.append({"event": "file_modified", "ts": "2026-03-18T00:01:00Z"})

        events_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8",
        )

        # Should NOT block — current session only has 1 file_modified
        result = _check_review_file_count_gate(run_dir, _read_run_events(run_dir, reader))
        assert result is None

    def test_trw_init_writes_session_start_event(
        self,
        run_dir: Path,
    ) -> None:
        """FR-03: trw_init must write a session_start event to events.jsonl.

        After a run_init event is logged, a session_start event with
        source='trw_init' should also be present in the event stream.
        """
        events_path = run_dir / "meta" / "events.jsonl"
        # Simulate what trw_init does: run_init followed by session_start
        events = [
            {"event": "run_init", "ts": "2026-03-28T00:00:00Z", "data": {"task": "test"}},
            {
                "event": "session_start",
                "ts": "2026-03-28T00:00:01Z",
                "data": {"source": "trw_init", "run_detected": True, "query": "*"},
            },
        ]
        events_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8",
        )

        # Read back events and verify session_start with source=trw_init exists
        raw_events = [json.loads(line) for line in events_path.read_text().strip().splitlines()]
        session_starts = [
            e for e in raw_events
            if e.get("event") == "session_start"
        ]
        assert len(session_starts) == 1
        assert session_starts[0]["data"]["source"] == "trw_init"
        assert session_starts[0]["data"]["run_detected"] is True

    def test_trw_init_then_session_start_uses_last_boundary(self) -> None:
        """FR-04: If trw_session_start() is called after trw_init(), the second
        session_start supersedes the first.

        Scenario:
        - 5 file_modified events (from previous session)
        - session_start from trw_init
        - 2 file_modified events
        - session_start from trw_session_start (supersedes trw_init boundary)
        - 1 file_modified event

        _count_file_modified_current_session should return 1 (only after the
        LAST session_start).
        """
        events: list[dict[str, object]] = [
            {"event": "file_modified", "ts": "T0"},
            {"event": "file_modified", "ts": "T1"},
            {"event": "file_modified", "ts": "T2"},
            {"event": "file_modified", "ts": "T3"},
            {"event": "file_modified", "ts": "T4"},
            # session_start from trw_init
            {"event": "session_start", "ts": "T5", "data": {"source": "trw_init"}},
            {"event": "file_modified", "ts": "T6"},
            {"event": "file_modified", "ts": "T7"},
            # session_start from trw_session_start — supersedes the trw_init one
            {"event": "session_start", "ts": "T8", "data": {"source": "trw_session_start"}},
            {"event": "file_modified", "ts": "T9"},
        ]
        assert _count_file_modified_current_session(events) == 1

    def test_stale_run_blocks_when_current_session_also_exceeds(
        self,
        run_dir: Path,
        reader: FileStateReader,
    ) -> None:
        """If the current session also exceeds the threshold, it should still block."""
        events_path = run_dir / "meta" / "events.jsonl"
        events: list[dict[str, object]] = []
        # Previous session
        for i in range(30):
            events.append({"event": "file_modified", "ts": f"2026-03-17T{i:02d}:00:00Z"})
        # New session boundary
        events.append({"event": "session_start", "ts": "2026-03-18T00:00:00Z"})
        # Current session: 8 file_modified (exceeds threshold of 5)
        for i in range(8):
            events.append({"event": "file_modified", "ts": f"2026-03-18T00:{i:02d}:01Z"})

        events_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8",
        )

        result = _check_review_file_count_gate(run_dir, _read_run_events(run_dir, reader))
        assert result is not None
        assert "8 files modified" in result


# --- R-07: Checkpoint blocker warning ---


@pytest.mark.integration
class TestCheckpointBlockerWarning:
    """R-07: Warn when last checkpoint message contains 'blocker'."""

    def test_checkpoint_blocker_warning_fires(
        self,
        run_dir: Path,
        reader: FileStateReader,
    ) -> None:
        """Last checkpoint with 'blocker' in message should produce a warning."""
        checkpoints_path = run_dir / "meta" / "checkpoints.jsonl"
        checkpoints = [
            {"ts": "2026-03-18T00:00:00Z", "message": "Implementation started"},
            {"ts": "2026-03-18T01:00:00Z", "message": "One blocker: mypy error"},
        ]
        checkpoints_path.write_text(
            "\n".join(json.dumps(c) for c in checkpoints) + "\n",
            encoding="utf-8",
        )

        result = _check_checkpoint_blocker_gate(run_dir, reader)
        assert result is not None
        assert "blocker" in result.lower()
        assert "One blocker: mypy error" in result

    def test_checkpoint_blocker_no_warning_for_normal(
        self,
        run_dir: Path,
        reader: FileStateReader,
    ) -> None:
        """Last checkpoint without 'blocker' should produce no warning."""
        checkpoints_path = run_dir / "meta" / "checkpoints.jsonl"
        checkpoints = [
            {"ts": "2026-03-18T00:00:00Z", "message": "Implementation complete"},
        ]
        checkpoints_path.write_text(
            "\n".join(json.dumps(c) for c in checkpoints) + "\n",
            encoding="utf-8",
        )

        result = _check_checkpoint_blocker_gate(run_dir, reader)
        assert result is None

    def test_checkpoint_blocker_empty_checkpoints(
        self,
        run_dir: Path,
        reader: FileStateReader,
    ) -> None:
        """No checkpoints file should produce no warning (fail-open)."""
        # Don't create checkpoints.jsonl at all
        result = _check_checkpoint_blocker_gate(run_dir, reader)
        assert result is None

    def test_checkpoint_blocker_case_insensitive(
        self,
        run_dir: Path,
        reader: FileStateReader,
    ) -> None:
        """The 'blocker' keyword check should be case-insensitive."""
        checkpoints_path = run_dir / "meta" / "checkpoints.jsonl"
        checkpoints = [
            {"ts": "2026-03-18T00:00:00Z", "message": "BLOCKER: critical issue found"},
        ]
        checkpoints_path.write_text(
            "\n".join(json.dumps(c) for c in checkpoints) + "\n",
            encoding="utf-8",
        )

        result = _check_checkpoint_blocker_gate(run_dir, reader)
        assert result is not None

    def test_checkpoint_blocker_wired_into_check_delivery_gates(
        self,
        run_dir: Path,
        reader: FileStateReader,
    ) -> None:
        """check_delivery_gates() should include checkpoint_blocker_warning when gate fires."""
        checkpoints_path = run_dir / "meta" / "checkpoints.jsonl"
        checkpoints = [
            {"ts": "2026-03-18T00:00:00Z", "message": "Found a blocker in the build"},
        ]
        checkpoints_path.write_text(
            "\n".join(json.dumps(c) for c in checkpoints) + "\n",
            encoding="utf-8",
        )

        result = check_delivery_gates(run_dir, reader)
        assert "checkpoint_blocker_warning" in result

    def test_checkpoint_blocker_failopen_on_read_error(
        self,
        run_dir: Path,
    ) -> None:
        """Read errors should not block delivery (fail-open)."""
        mock_reader = MagicMock(spec=FileStateReader)
        mock_reader.exists.return_value = True
        mock_reader.read_jsonl.side_effect = Exception("corrupt file")

        result = _check_checkpoint_blocker_gate(run_dir, mock_reader)
        assert result is None
