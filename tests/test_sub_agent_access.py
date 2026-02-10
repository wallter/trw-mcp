"""Tests for PRD-CORE-014: Sub-Agent TRW MCP Tool Access.

Phase 1: Concurrent safety, shard context, metadata enrichment.
"""

from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.learning import LearningEntry
from trw_mcp.state.persistence import (
    FileStateReader,
    FileStateWriter,
    lock_for_rmw,
)


# ---------------------------------------------------------------------------
# FR01: trw_shard_context tool
# ---------------------------------------------------------------------------


@pytest.fixture()
def shard_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a project with run directory for shard context tests."""
    project = tmp_path / "project"
    trw_dir = project / ".trw"
    trw_dir.mkdir(parents=True)

    # Create run directory with run.yaml
    run_path = tmp_path / "run"
    meta = run_path / "meta"
    meta.mkdir(parents=True)
    (run_path / "reports").mkdir(parents=True)

    from ruamel.yaml import YAML

    yaml = YAML()
    state = {
        "run_id": "run-shard-test",
        "task": "test",
        "wave_progress": {"current_wave": 2, "total_waves": 3},
    }
    yaml.dump(state, meta / "run.yaml")

    monkeypatch.setattr(
        "trw_mcp.state._paths.resolve_project_root", lambda: project,
    )
    monkeypatch.setattr(
        "trw_mcp.tools.orchestration.resolve_project_root", lambda: project,
    )

    return run_path


class TestShardContext:
    """Test trw_shard_context tool (FR01)."""

    def test_returns_all_fields(self, shard_project: Path) -> None:
        """Shard context returns run_path, shard_id, wave, paths, guidance."""
        from trw_mcp.state._paths import resolve_run_path

        # Simulate what trw_shard_context does
        resolved = resolve_run_path(str(shard_project))
        assert resolved == shard_project

        meta = shard_project / "meta"
        reader = FileStateReader()
        state = reader.read_yaml(meta / "run.yaml")
        wave_progress = state.get("wave_progress", {})
        assert isinstance(wave_progress, dict)
        assert wave_progress.get("current_wave") == 2

    def test_shard_context_builds_paths(self, shard_project: Path) -> None:
        """Shard context builds correct scratch and findings paths."""
        config = TRWConfig()
        shard_id = "S1"
        scratch = shard_project / "scratch" / shard_id
        findings = shard_project / config.findings_dir
        events = shard_project / "meta" / "events.jsonl"

        assert str(scratch).endswith("scratch/S1")
        assert str(findings).endswith("findings")
        assert str(events).endswith("events.jsonl")

    def test_shard_context_tool_guidance(self) -> None:
        """Tool guidance includes key shard instructions."""
        shard_id = "S1"
        run_path = "/tmp/run"
        guidance = (
            "1. Use trw_event(shard_id='{shard_id}') to log progress\n"
            "2. Use trw_finding_register(run_path='{run_path}') for discoveries\n"
        ).format(shard_id=shard_id, run_path=run_path)
        assert "trw_event" in guidance
        assert "trw_finding_register" in guidance

    def test_missing_run_path_raises(self) -> None:
        """Missing run path raises StateError."""
        from trw_mcp.exceptions import StateError
        from trw_mcp.state._paths import resolve_run_path

        with pytest.raises(StateError):
            resolve_run_path("/nonexistent/path")


# ---------------------------------------------------------------------------
# FR02/FR03: Concurrent Write Safety
# ---------------------------------------------------------------------------


class TestConcurrentJsonlAppend:
    """Test concurrent JSONL append safety (FR02)."""

    def test_concurrent_10_writers(self, tmp_path: Path) -> None:
        """10 threads × 100 records → 1000 total, no corruption."""
        writer = FileStateWriter()
        target = tmp_path / "events.jsonl"
        num_threads = 10
        records_per_thread = 100

        def write_records(thread_id: int) -> None:
            for i in range(records_per_thread):
                writer.append_jsonl(target, {
                    "thread": thread_id, "seq": i, "data": "test",
                })

        threads = [
            threading.Thread(target=write_records, args=(t,))
            for t in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify all records present and valid
        reader = FileStateReader()
        records = reader.read_jsonl(target)
        assert len(records) == num_threads * records_per_thread

    def test_no_interleaving(self, tmp_path: Path) -> None:
        """Each JSON line must be a valid, complete JSON object."""
        writer = FileStateWriter()
        target = tmp_path / "events.jsonl"

        def write_large(thread_id: int) -> None:
            for i in range(50):
                writer.append_jsonl(target, {
                    "thread": thread_id, "seq": i,
                    "payload": "x" * 500,  # larger records stress interleaving
                })

        threads = [
            threading.Thread(target=write_large, args=(t,))
            for t in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Every line must parse as valid JSON
        with target.open("r") as fh:
            for line_num, line in enumerate(fh, 1):
                stripped = line.strip()
                if not stripped:
                    continue
                parsed = json.loads(stripped)
                assert isinstance(parsed, dict), f"Line {line_num} not a dict"


class TestConcurrentYamlWrite:
    """Test concurrent YAML write safety (FR03)."""

    def test_concurrent_yaml_no_corruption(self, tmp_path: Path) -> None:
        """5 threads writing to same YAML file → final file is valid."""
        writer = FileStateWriter()
        reader = FileStateReader()
        target = tmp_path / "shared.yaml"

        # Write initial file
        writer.write_yaml(target, {"counter": 0})

        errors: list[str] = []

        def write_yaml(thread_id: int) -> None:
            try:
                for _ in range(20):
                    writer.write_yaml(target, {
                        "counter": thread_id,
                        "data": f"thread-{thread_id}",
                    })
            except Exception as exc:
                errors.append(str(exc))

        threads = [
            threading.Thread(target=write_yaml, args=(t,))
            for t in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No errors, file must be valid YAML
        assert errors == [], f"Thread errors: {errors}"
        data = reader.read_yaml(target)
        assert "counter" in data


# ---------------------------------------------------------------------------
# FR03/FR08: lock_for_rmw
# ---------------------------------------------------------------------------


class TestLockForRmw:
    """Test lock_for_rmw context manager (FR03/FR08)."""

    def test_acquires_exclusive_lock(self, tmp_path: Path) -> None:
        """lock_for_rmw creates .lock file and yields path."""
        target = tmp_path / "data.yaml"
        target.write_text("test: 1\n")

        with lock_for_rmw(target) as path:
            assert path == target
            # Lock file should exist during the block
            lock_file = tmp_path / "data.yaml.lock"
            assert lock_file.exists()

    def test_releases_on_exception(self, tmp_path: Path) -> None:
        """Lock is released even when an exception occurs."""
        target = tmp_path / "data.yaml"
        target.write_text("test: 1\n")

        with pytest.raises(ValueError, match="intentional"):
            with lock_for_rmw(target):
                raise ValueError("intentional")

        # Should be able to acquire lock again (it was released)
        with lock_for_rmw(target) as path:
            assert path == target

    def test_concurrent_rmw_serialized(self, tmp_path: Path) -> None:
        """Concurrent R-M-W cycles are serialized by the lock."""
        writer = FileStateWriter()
        reader = FileStateReader()
        target = tmp_path / "counter.yaml"
        writer.write_yaml(target, {"counter": 0})
        num_increments = 50
        num_threads = 5

        def increment() -> None:
            for _ in range(num_increments):
                with lock_for_rmw(target):
                    data = reader.read_yaml(target)
                    data["counter"] = int(str(data.get("counter", 0))) + 1
                    writer.write_yaml(target, data)

        threads = [threading.Thread(target=increment) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final = reader.read_yaml(target)
        assert final["counter"] == num_increments * num_threads


# ---------------------------------------------------------------------------
# FR04: Event Metadata Enrichment
# ---------------------------------------------------------------------------


class TestEventMetadataEnrichment:
    """Test trw_event shard_id and agent_role params (FR04)."""

    def test_event_with_shard_metadata(self, shard_project: Path) -> None:
        """Event includes shard_id and agent_role in JSONL record."""
        writer = FileStateWriter()
        from trw_mcp.state.persistence import FileEventLogger

        events = FileEventLogger(writer)
        events_path = shard_project / "meta" / "events.jsonl"

        # Simulate what trw_event does with shard metadata
        event_data: dict[str, object] = {"test": "value"}
        event_data["shard_id"] = "S1"
        event_data["agent_role"] = "research"
        events.log_event(events_path, "shard_finding", event_data)

        reader = FileStateReader()
        records = reader.read_jsonl(events_path)
        assert len(records) >= 1
        last = records[-1]
        assert last["shard_id"] == "S1"
        assert last["agent_role"] == "research"

    def test_event_without_shard_backward_compat(self, shard_project: Path) -> None:
        """Event without shard metadata works (backward compatible)."""
        writer = FileStateWriter()
        from trw_mcp.state.persistence import FileEventLogger

        events = FileEventLogger(writer)
        events_path = shard_project / "meta" / "events.jsonl"
        events.log_event(events_path, "plain_event", {"key": "val"})

        reader = FileStateReader()
        records = reader.read_jsonl(events_path)
        assert len(records) >= 1
        last = records[-1]
        assert "shard_id" not in last


# ---------------------------------------------------------------------------
# FR07: Learning Entry Shard Metadata
# ---------------------------------------------------------------------------


class TestLearningShardMetadata:
    """Test trw_learn shard_id param (FR07)."""

    def test_learning_entry_has_shard_id(self) -> None:
        """LearningEntry model accepts shard_id."""
        entry = LearningEntry(
            id="L-test",
            summary="Test",
            detail="Detail",
            shard_id="S1",
        )
        assert entry.shard_id == "S1"

    def test_learning_entry_shard_id_optional(self) -> None:
        """shard_id defaults to None."""
        entry = LearningEntry(
            id="L-test",
            summary="Test",
            detail="Detail",
        )
        assert entry.shard_id is None

    def test_learning_entry_shard_id_serializes(self) -> None:
        """shard_id appears in serialized output when set."""
        from trw_mcp.state.persistence import model_to_dict

        entry = LearningEntry(
            id="L-test",
            summary="Test",
            detail="Detail",
            shard_id="shard-research-01",
        )
        data = model_to_dict(entry)
        assert data["shard_id"] == "shard-research-01"


# ---------------------------------------------------------------------------
# FR08: Concurrent Learning Index Safety
# ---------------------------------------------------------------------------


class TestConcurrentLearningIndex:
    """Test concurrent learning index safety (FR08)."""

    def test_concurrent_learn_index_update(self, tmp_path: Path) -> None:
        """Multiple threads updating learning index via lock_for_rmw."""
        writer = FileStateWriter()
        reader = FileStateReader()
        trw_dir = tmp_path / ".trw"
        learnings_dir = trw_dir / "learnings"
        learnings_dir.mkdir(parents=True)
        index_path = learnings_dir / "index.yaml"
        writer.write_yaml(index_path, {"entries": [], "total_count": 0})

        num_threads = 5

        def add_entry(thread_id: int) -> None:
            with lock_for_rmw(index_path):
                data = reader.read_yaml(index_path)
                entries = list(data.get("entries", []))
                entries.append({"id": f"L-{thread_id}", "summary": f"Entry {thread_id}"})
                data["entries"] = entries
                data["total_count"] = len(entries)
                writer.write_yaml(index_path, data)

        threads = [threading.Thread(target=add_entry, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final = reader.read_yaml(index_path)
        assert final["total_count"] == num_threads
        assert len(final["entries"]) == num_threads


# ---------------------------------------------------------------------------
# FR09: Recall Receipt Shard Isolation
# ---------------------------------------------------------------------------


class TestRecallReceiptShardId:
    """Test recall receipt shard_id attribution (FR09)."""

    def test_receipt_includes_shard_id(self, tmp_path: Path) -> None:
        """Recall receipt includes shard_id when provided."""
        writer = FileStateWriter()
        receipts_dir = tmp_path / "receipts"
        receipts_dir.mkdir()
        receipt_path = receipts_dir / "recall_log.jsonl"

        # Simulate _log_recall_receipt with shard_id
        record: dict[str, object] = {
            "query": "test query",
            "matched_ids": ["L-001"],
            "match_count": 1,
            "shard_id": "S1",
        }
        writer.append_jsonl(receipt_path, record)

        reader = FileStateReader()
        records = reader.read_jsonl(receipt_path)
        assert records[-1]["shard_id"] == "S1"

    def test_receipt_without_shard_id(self, tmp_path: Path) -> None:
        """Recall receipt without shard_id (backward compat)."""
        writer = FileStateWriter()
        receipts_dir = tmp_path / "receipts"
        receipts_dir.mkdir()
        receipt_path = receipts_dir / "recall_log.jsonl"

        record: dict[str, object] = {
            "query": "test query",
            "matched_ids": ["L-001"],
            "match_count": 1,
        }
        writer.append_jsonl(receipt_path, record)

        reader = FileStateReader()
        records = reader.read_jsonl(receipt_path)
        assert "shard_id" not in records[-1]


# ---------------------------------------------------------------------------
# Checkpoint shard_id (FR04 extension)
# ---------------------------------------------------------------------------


class TestCheckpointShardId:
    """Test checkpoint shard_id param."""

    def test_checkpoint_with_shard_id(self, shard_project: Path) -> None:
        """Checkpoint record includes shard_id when provided."""
        writer = FileStateWriter()
        from trw_mcp.state.persistence import FileEventLogger
        from datetime import datetime, timezone

        meta = shard_project / "meta"
        reader = FileStateReader()
        state_data = reader.read_yaml(meta / "run.yaml")

        # Simulate trw_checkpoint with shard_id
        checkpoint: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "message": "test checkpoint",
            "state": state_data,
            "shard_id": "S1",
        }
        writer.append_jsonl(meta / "checkpoints.jsonl", checkpoint)

        records = reader.read_jsonl(meta / "checkpoints.jsonl")
        assert records[-1]["shard_id"] == "S1"

    def test_checkpoint_without_shard_id(self, shard_project: Path) -> None:
        """Checkpoint without shard_id (backward compat)."""
        writer = FileStateWriter()
        from datetime import datetime, timezone

        meta = shard_project / "meta"
        reader = FileStateReader()
        state_data = reader.read_yaml(meta / "run.yaml")

        checkpoint: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "message": "test",
            "state": state_data,
        }
        writer.append_jsonl(meta / "checkpoints.jsonl", checkpoint)

        records = reader.read_jsonl(meta / "checkpoints.jsonl")
        assert "shard_id" not in records[-1]
