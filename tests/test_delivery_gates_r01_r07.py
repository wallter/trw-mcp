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
