"""Tests for PRD-FIX-027 process_outcome behavior."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from trw_mcp.models.run import EventType
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


class TestProcessOutcomeForEvent:
    """FR03/FR04: process_outcome_for_event integration tests."""

    def test_returns_list(self) -> None:
        """process_outcome_for_event always returns a list."""
        from trw_mcp.scoring import process_outcome_for_event as poe

        result = poe(EventType.SHARD_STARTED)
        assert isinstance(result, list)

    def test_returns_list_for_unknown_event(self) -> None:
        """Unknown events return empty list (no reward)."""
        from trw_mcp.scoring import process_outcome_for_event as poe

        result = poe("totally_unknown_xyz_event")
        assert result == []

    def test_build_passed_event_type_string(self) -> None:
        """EventType.BUILD_PASSED string value is 'build_passed'."""
        assert EventType.BUILD_PASSED == "build_passed"

    def test_build_failed_event_type_string(self) -> None:
        """EventType.BUILD_FAILED string value is 'build_failed'."""
        assert EventType.BUILD_FAILED == "build_failed"

    def test_outcome_history_appended(self, tmp_path: Path) -> None:
        """Outcome history grows with each process_outcome call."""
        import trw_mcp.scoring as _sc
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.scoring import process_outcome as po

        writer = FileStateWriter()
        reader = FileStateReader()

        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        logs_dir = trw_dir / "logs"
        logs_dir.mkdir(parents=True)

        entry: dict[str, object] = {
            "id": "L-hist001",
            "summary": "history test",
            "detail": "detail",
            "impact": 0.7,
            "status": "active",
            "q_value": 0.7,
            "q_observations": 0,
            "recurrence": 1,
            "outcome_history": [],
            "tags": [],
        }
        entry_path = entries_dir / "L-hist001.yaml"
        writer.write_yaml(entry_path, entry)

        now_iso = datetime.now(timezone.utc).isoformat()
        receipt: dict[str, object] = {
            "ts": now_iso,
            "matched_ids": ["L-hist001"],
            "query": "history",
        }
        writer.append_jsonl(logs_dir / "recall_tracking.jsonl", receipt)

        old_config = _sc._config
        old_reader = _sc._reader
        old_writer = _sc._writer

        cfg = TRWConfig()
        object.__setattr__(cfg, "learning_outcome_correlation_window_minutes", 9999)
        object.__setattr__(cfg, "learning_outcome_correlation_scope", "window")
        _sc._config = cfg
        _sc._reader = reader
        _sc._writer = writer

        try:
            po(trw_dir, 0.8, "build_passed")
            stored = reader.read_yaml(entry_path)
            history = stored.get("outcome_history", [])
            assert isinstance(history, list)
            assert len(history) >= 1
        finally:
            _sc._config = old_config
            _sc._reader = old_reader
            _sc._writer = old_writer

    def test_negative_outcome_decreases_q_value(self, tmp_path: Path) -> None:
        """Negative reward (build_failed) decreases q_value for correlated entries."""
        import trw_mcp.scoring as _sc
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.scoring import process_outcome as po

        writer = FileStateWriter()
        reader = FileStateReader()

        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        logs_dir = trw_dir / "logs"
        logs_dir.mkdir(parents=True)

        entry: dict[str, object] = {
            "id": "L-neg001",
            "summary": "negative reward test",
            "detail": "detail",
            "impact": 0.8,
            "status": "active",
            "q_value": 0.8,
            "q_observations": 0,
            "recurrence": 1,
            "tags": [],
        }
        entry_path = entries_dir / "L-neg001.yaml"
        writer.write_yaml(entry_path, entry)

        now_iso = datetime.now(timezone.utc).isoformat()
        receipt: dict[str, object] = {
            "ts": now_iso,
            "matched_ids": ["L-neg001"],
            "query": "test",
        }
        writer.append_jsonl(logs_dir / "recall_tracking.jsonl", receipt)

        old_config = _sc._config
        old_reader = _sc._reader
        old_writer = _sc._writer

        cfg = TRWConfig()
        object.__setattr__(cfg, "learning_outcome_correlation_window_minutes", 9999)
        object.__setattr__(cfg, "learning_outcome_correlation_scope", "window")
        _sc._config = cfg
        _sc._reader = reader
        _sc._writer = writer

        try:
            po(trw_dir, -0.4, "build_failed")
            stored = reader.read_yaml(entry_path)
            q_new = float(str(stored.get("q_value", 0.8)))
            assert q_new < 0.8, "Negative reward should decrease q_value"
        finally:
            _sc._config = old_config
            _sc._reader = old_reader
            _sc._writer = old_writer
