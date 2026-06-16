"""Tests for ceremony helpers and core delivery sub-steps."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from trw_mcp.state._paths import find_active_run, find_run_via_mtime_scan
from trw_mcp.tools.ceremony import _do_reflect, _get_run_status
from trw_mcp.tools.checkpoint import _do_checkpoint

from ._tools_ceremony_support import (
    run_dir,  # noqa: F401
    trw_project,  # noqa: F401
)


class TestFindActiveRun:
    """Helper function for locating active runs."""

    def test_returns_none_when_no_runs_root(self, tmp_path: Path) -> None:
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig()
        object.__setattr__(cfg, "runs_root", "nonexistent")
        with patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path):
            with patch("trw_mcp.state._paths.get_config", return_value=cfg):
                result = find_active_run()
        assert result is None

    def test_finds_run_directory(self, tmp_path: Path, run_dir: Path) -> None:
        # PRD-FIX-085: find_active_run() is pin-only; the disk-scan discovery
        # this asserts now lives in find_run_via_mtime_scan(). Use it to keep
        # the original scan-behavior intent without weakening the assertion.
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig()
        object.__setattr__(cfg, "runs_root", ".trw/runs")
        with patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path):
            with patch("trw_mcp.state._paths.get_config", return_value=cfg):
                result = find_run_via_mtime_scan()
        assert result is not None
        assert "20260211T120000Z-test" in str(result)


class TestGetRunStatus:
    """Status extraction from run directory."""

    def test_extracts_status(self, run_dir: Path) -> None:
        result = _get_run_status(run_dir)
        assert result["phase"] == "implement"
        assert result["status"] == "active"
        assert result["task_name"] == "test-task"

    def test_handles_missing_run_yaml(self, tmp_path: Path) -> None:
        result = _get_run_status(tmp_path)
        assert result["active_run"] == str(tmp_path)


class TestDoCheckpoint:
    """Checkpoint creation during delivery."""

    def test_creates_checkpoint_file(self, run_dir: Path) -> None:
        _do_checkpoint(run_dir, "delivery")
        cp_path = run_dir / "meta" / "checkpoints.jsonl"
        assert cp_path.exists()
        data = json.loads(cp_path.read_text(encoding="utf-8").strip())
        assert data["message"] == "delivery"
        assert "ts" in data

    def test_appends_checkpoint_event(self, run_dir: Path) -> None:
        _do_checkpoint(run_dir, "delivery")
        events_path = run_dir / "meta" / "events.jsonl"
        lines = [line for line in events_path.read_text(encoding="utf-8").strip().split("\n") if line]
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["event"] == "checkpoint"


class TestDoReflect:
    """Reflection during delivery ceremony."""

    def test_returns_success_with_empty_events(self, trw_project: Path) -> None:
        trw_dir = trw_project / ".trw"
        result = _do_reflect(trw_dir, None)
        assert result["status"] == "success"
        assert result["events_analyzed"] == 0

    def test_analyzes_events_from_run(self, trw_project: Path, run_dir: Path) -> None:
        events_path = run_dir / "meta" / "events.jsonl"
        events = [
            {"ts": "2026-02-11T12:00:00Z", "event": "phase_enter", "data": {"phase": "implement"}},
            {"ts": "2026-02-11T12:01:00Z", "event": "shard_complete", "data": {}},
        ]
        events_path.write_text(
            "\n".join(json.dumps(event) for event in events) + "\n",
            encoding="utf-8",
        )
        trw_dir = trw_project / ".trw"
        result = _do_reflect(trw_dir, run_dir)
        assert result["status"] == "success"
        assert result["events_analyzed"] == 2

    def test_torn_line_does_not_abort_reflection(
        self,
        trw_project: Path,
        run_dir: Path,
    ) -> None:
        """A single torn/corrupt line in events.jsonl must not erase all learnings.

        Append-only events.jsonl can carry a torn concurrent append (two writers
        interleaving a partial record). The delivery reflection seam must degrade
        to "drop that one line", not abort the whole read and lose every
        mechanically extracted learning. Mirrors the resilient reader already used
        on this same log by ``agent_work_evidence`` (regression guard).
        """
        events_path = run_dir / "meta" / "events.jsonl"
        valid_before = {
            "ts": "2026-02-11T12:00:00Z",
            "event": "build_error",
            "data": {"msg": "compile failed"},
        }
        valid_after = {
            "ts": "2026-02-11T12:02:00Z",
            "event": "validation_failure",
            "data": {"msg": "tests red"},
        }
        # Middle line is a torn append: valid JSON prefix, truncated mid-object.
        events_path.write_text(
            json.dumps(valid_before)
            + "\n"
            + '{"ts": "2026-02-11T12:01:00Z", "event": "tor\n'
            + json.dumps(valid_after)
            + "\n",
            encoding="utf-8",
        )
        trw_dir = trw_project / ".trw"

        # Before the fix this raised StateError, aborting delivery reflection.
        result = _do_reflect(trw_dir, run_dir)

        assert result["status"] == "success"
        # Only the two intact lines survive; the torn line is dropped, not fatal.
        assert result["events_analyzed"] == 2
        # Both surviving lines are error events → mechanical learnings extracted.
        assert result["learnings_produced"] >= 1

    def test_no_telemetry_noise_learnings_from_success_patterns(
        self,
        trw_project: Path,
        run_dir: Path,
    ) -> None:
        """PRD-FIX-021: _do_reflect must not create 'Success: X (Nx)' learnings."""
        events_path = run_dir / "meta" / "events.jsonl"
        success_events = [{"ts": f"2026-02-11T12:0{i}:00Z", "event": "shard_complete", "data": {}} for i in range(8)]
        events_path.write_text(
            "\n".join(json.dumps(event) for event in success_events) + "\n",
            encoding="utf-8",
        )
        trw_dir = trw_project / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        before_count = len(list(entries_dir.glob("*.yaml")))

        _do_reflect(trw_dir, run_dir)

        after_entries = list(entries_dir.glob("*.yaml"))
        new_entries = after_entries[before_count:]
        for entry_file in new_entries:
            from trw_mcp.state.persistence import FileStateReader

            data = FileStateReader().read_yaml(entry_file)
            summary = str(data.get("summary", ""))
            assert not summary.startswith("Success:"), f"Telemetry noise learning created: {summary}"

    def test_no_telemetry_noise_learnings_from_repeated_ops(
        self,
        trw_project: Path,
        run_dir: Path,
    ) -> None:
        """PRD-FIX-021: _do_reflect must not create 'Repeated operation: X' learnings."""
        events_path = run_dir / "meta" / "events.jsonl"
        repeated_events = [{"ts": f"2026-02-11T12:0{i}:00Z", "event": "checkpoint", "data": {}} for i in range(6)]
        events_path.write_text(
            "\n".join(json.dumps(event) for event in repeated_events) + "\n",
            encoding="utf-8",
        )
        trw_dir = trw_project / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        before_count = len(list(entries_dir.glob("*.yaml")))

        _do_reflect(trw_dir, run_dir)

        after_entries = list(entries_dir.glob("*.yaml"))
        new_entries = after_entries[before_count:]
        for entry_file in new_entries:
            from trw_mcp.state.persistence import FileStateReader

            data = FileStateReader().read_yaml(entry_file)
            summary = str(data.get("summary", ""))
            assert not summary.startswith("Repeated operation:"), f"Telemetry noise learning created: {summary}"
