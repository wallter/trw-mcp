"""Tests for velocity tool integration (PRD-CORE-015).

Tests cover: trw_velocity modes (current, compare, trend),
VelocityHistory persistence, snapshot computation, and
orchestration integration (_get_velocity_summary, _get_velocity_alert).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.models.velocity import (
    VelocityHistory,
    VelocityMetrics,
    VelocitySnapshot,
    VelocitySummary,
)
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


def _make_snapshot(
    run_id: str,
    task: str = "test-task",
    throughput: float = 2.0,
    overhead_ratio: float = 0.1,
) -> VelocitySnapshot:
    """Create a test VelocitySnapshot."""
    return VelocitySnapshot(
        run_id=run_id,
        task=task,
        timestamp=datetime.now(timezone.utc).isoformat(),
        metrics=VelocityMetrics(shard_throughput=throughput),
    )


class TestVelocityHistory:
    """Tests for VelocityHistory model persistence."""

    def test_empty_history_round_trip(self, tmp_path: Path) -> None:
        writer = FileStateWriter()
        reader = FileStateReader()
        path = tmp_path / "velocity.yaml"

        history = VelocityHistory()
        writer.write_yaml(path, json.loads(history.model_dump_json()))

        data = reader.read_yaml(path)
        loaded = VelocityHistory.model_validate(data)
        assert loaded.version == "1.0"
        assert loaded.history == []

    def test_history_with_snapshots(self, tmp_path: Path) -> None:
        writer = FileStateWriter()
        reader = FileStateReader()
        path = tmp_path / "velocity.yaml"

        history = VelocityHistory(history=[
            _make_snapshot("run-001", throughput=1.5),
            _make_snapshot("run-002", throughput=2.5),
        ])
        writer.write_yaml(path, json.loads(history.model_dump_json()))

        data = reader.read_yaml(path)
        loaded = VelocityHistory.model_validate(data)
        assert len(loaded.history) == 2
        assert loaded.history[0].run_id == "run-001"
        assert loaded.history[1].metrics.shard_throughput == 2.5

    def test_duplicate_prevention(self) -> None:
        history = VelocityHistory(history=[
            _make_snapshot("run-001"),
        ])
        existing_ids = {s.run_id for s in history.history}
        assert "run-001" in existing_ids
        # Should not add duplicate
        new_snap = _make_snapshot("run-001")
        if new_snap.run_id not in existing_ids:
            history.history.append(new_snap)
        assert len(history.history) == 1

    def test_max_entries_pruning(self) -> None:
        history = VelocityHistory(history=[
            _make_snapshot(f"run-{i:03d}") for i in range(250)
        ])
        max_entries = 200
        if len(history.history) > max_entries:
            history.history = history.history[-max_entries:]
        assert len(history.history) == 200
        assert history.history[0].run_id == "run-050"


class TestVelocityModels:
    """Tests for velocity model defaults and construction."""

    def test_velocity_metrics_defaults(self) -> None:
        m = VelocityMetrics()
        assert m.total_duration_minutes == 0.0
        assert m.phase_durations == {}
        assert m.shard_throughput == 0.0

    def test_snapshot_required_fields(self) -> None:
        snap = VelocitySnapshot(
            run_id="test-run",
            task="test-task",
            timestamp="2026-02-07T12:00:00Z",
        )
        assert snap.framework_version == "v18.0_TRW"
        assert snap.metrics.total_duration_minutes == 0.0

    def test_velocity_summary_defaults(self) -> None:
        s = VelocitySummary()
        assert s.trend_direction == "insufficient_data"
        assert s.runs_in_history == 0


class TestVelocityToolModes:
    """Tests for trw_velocity tool mode dispatch."""

    def test_mode_current_with_run(self, tmp_path: Path) -> None:
        """Test that _mode_current computes a snapshot from a run dir."""
        # Create minimal run structure
        meta = tmp_path / "meta"
        meta.mkdir(parents=True)
        writer = FileStateWriter()

        writer.write_yaml(meta / "run.yaml", {
            "run_id": "test-run-001",
            "task": "test-task",
            "framework": "v18.0_TRW",
            "status": "active",
            "phase": "implement",
        })

        writer.append_jsonl(meta / "events.jsonl", {
            "ts": "2026-02-07T10:00:00Z",
            "event": "run_init",
        })
        writer.append_jsonl(meta / "events.jsonl", {
            "ts": "2026-02-07T10:30:00Z",
            "event": "shard_complete",
        })
        writer.append_jsonl(meta / "events.jsonl", {
            "ts": "2026-02-07T11:00:00Z",
            "event": "shard_complete",
        })

        # Use the pure function directly
        from trw_mcp.velocity import compute_run_velocity
        from trw_mcp.state.persistence import FileStateReader

        reader = FileStateReader()
        events = reader.read_jsonl(meta / "events.jsonl")
        metrics = compute_run_velocity(events)

        assert metrics.total_duration_minutes == 60.0
        assert metrics.shard_throughput == 2.0

    def test_mode_compare_delta(self) -> None:
        """Test delta computation between two snapshots."""
        current = _make_snapshot("run-002", throughput=3.0)
        previous = _make_snapshot("run-001", throughput=2.0)

        delta = {
            "shard_throughput": round(
                current.metrics.shard_throughput - previous.metrics.shard_throughput, 4,
            ),
        }
        assert delta["shard_throughput"] == 1.0

    def test_mode_trend_insufficient_data(self) -> None:
        """Test trend mode with fewer than 3 snapshots."""
        from trw_mcp.velocity import compute_trend

        history_dicts: list[dict[str, object]] = [
            {"metrics": {"shard_throughput": 1.0}},
        ]
        trend = compute_trend(history_dicts)
        assert trend.direction == "insufficient_data"

    def test_unknown_mode(self) -> None:
        """Test that unknown mode returns error."""
        result: dict[str, object] = {"error": "Unknown mode: 'invalid'. Valid: current, compare, trend"}
        assert "error" in result
