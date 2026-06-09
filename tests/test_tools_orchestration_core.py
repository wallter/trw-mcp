"""Core orchestration tool tests — module size, init, status, checkpoint."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

from tests._tools_orchestration_support import FRAMEWORK_VERSION, orch_tools, set_project_root  # noqa: F401
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._orchestration_phase import (
    _check_framework_version_staleness,
    _compute_reversion_metrics,
    _compute_wave_progress,
)


def test_orchestration_module_stays_within_500_lines() -> None:
    """CORE-089 keeps orchestration.py at or under the documented size gate."""
    module_path = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "tools" / "orchestration.py"
    assert sum(1 for _ in module_path.open("r", encoding="utf-8")) <= 500


@pytest.mark.parametrize(
    "helper",
    [
        _compute_wave_progress,
        _compute_reversion_metrics,
        _check_framework_version_staleness,
    ],
)
def test_phase_helpers_live_in_orchestration_phase(helper: object) -> None:
    """CORE-089 phase helpers should be defined in _orchestration_phase.py."""
    source_file = inspect.getsourcefile(helper)
    assert source_file is not None
    assert source_file.endswith("_orchestration_phase.py")


class TestTrwInit:
    """Tests for trw_init tool."""

    def test_creates_trw_dir(self, tmp_path: Path, orch_tools: dict[str, Any]) -> None:
        result = orch_tools["trw_init"].fn(task_name="test-task", objective="Test objective")

        assert "run_id" in result
        assert "run_path" in result
        assert result["status"] == "initialized"
        assert len(result["task_profile_hash"]) == 16

        trw_dir = tmp_path / ".trw"
        assert trw_dir.exists()
        assert (trw_dir / "config.yaml").exists()
        assert (trw_dir / "learnings" / "entries").exists()
        assert (trw_dir / "reflections").exists()
        assert (trw_dir / "scripts").exists()
        assert (trw_dir / "patterns").exists()
        assert (trw_dir / "context").exists()
        assert (trw_dir / ".gitignore").exists()

    def test_creates_run_dirs(self, orch_tools: dict[str, Any]) -> None:
        result = orch_tools["trw_init"].fn(task_name="my-task")

        run_path = Path(result["run_path"])
        assert (run_path / "meta" / "run.yaml").exists()
        assert (run_path / "meta" / "events.jsonl").exists()
        assert (run_path / "reports").exists()
        assert (run_path / "scratch" / "_orchestrator").exists()
        assert (run_path / "shards").exists()

    def test_run_yaml_content(self, orch_tools: dict[str, Any]) -> None:
        result = orch_tools["trw_init"].fn(task_name="check-task")

        reader = FileStateReader()
        run_yaml = reader.read_yaml(Path(result["run_path"]) / "meta" / "run.yaml")
        assert run_yaml["task"] == "check-task"
        assert run_yaml["framework"] == FRAMEWORK_VERSION
        assert run_yaml["status"] == "active"
        assert run_yaml["phase"] == "research"
        assert run_yaml["task_profile"]["complexity_class"] == "STANDARD"
        assert len(run_yaml["task_profile"]["profile_hash"]) == 16

    def test_init_easy_hint_persists_minimal_task_profile(self, orch_tools: dict[str, Any]) -> None:
        result = orch_tools["trw_init"].fn(task_name="easy-task", complexity_hint="EASY")

        reader = FileStateReader()
        run_yaml = reader.read_yaml(Path(result["run_path"]) / "meta" / "run.yaml")
        assert run_yaml["complexity_class"] == "MINIMAL"
        assert run_yaml["task_profile"]["complexity_class"] == "MINIMAL"
        assert run_yaml["task_profile"]["ceremony_depth"] == "light"
        assert "VALIDATE" in run_yaml["task_profile"]["mandatory_phases"]


class TestTrwStatus:
    """Tests for trw_status tool."""

    def test_reads_run_state(self, orch_tools: dict[str, Any]) -> None:
        init_result = orch_tools["trw_init"].fn(task_name="status-task")
        run_path = init_result["run_path"]

        status = orch_tools["trw_status"].fn(run_path=run_path)
        assert status["task"] == "status-task"
        assert status["phase"] == "research"
        assert status["status"] == "active"
        assert status["event_count"] >= 1
        assert status["phase_durations"]["active_phase"] == "research"
        assert status["phase_durations"]["phase_seconds"]["research"] >= 0.0

    def test_torn_events_line_does_not_abort_status(self, orch_tools: dict[str, Any]) -> None:
        """A torn concurrent append in events.jsonl must not brick trw_status.

        events.jsonl is an append-only log that trw_status reads only for
        advisory analytics (event_count, reflection, phase_durations,
        reversions); the authoritative run state lives in run.yaml. A single
        torn append (two writers interleaving a partial record) must degrade to
        "drop that one line", not raise StateError and abort the whole status
        read — trw_status is invoked on every resume/compaction, so aborting it
        blinds the agent to its own run. Mirrors the resilient-read fixes
        already applied to the _do_reflect and collect_reflection_inputs seams
        over this same log (regression guard).
        """
        init_result = orch_tools["trw_init"].fn(task_name="torn-status-task")
        run_path = init_result["run_path"]

        events_path = Path(run_path) / "meta" / "events.jsonl"
        existing = events_path.read_text(encoding="utf-8")
        # Append a torn line (valid JSON prefix, truncated mid-object) followed
        # by an intact event. Before the fix the torn line raised StateError.
        torn = '{"ts": "2026-02-11T12:01:00Z", "type": "phase_chan\n'
        intact = '{"ts": "2026-02-11T12:02:00Z", "type": "checkpoint", "phase": "research"}\n'
        events_path.write_text(existing + torn + intact, encoding="utf-8")

        status = orch_tools["trw_status"].fn(run_path=run_path)

        # Status still resolves; authoritative fields come from run.yaml.
        assert status["task"] == "torn-status-task"
        assert status["status"] == "active"
        # The torn line is dropped, not fatal; intact events still counted.
        assert status["event_count"] >= 1


class TestTrwCheckpoint:
    """Tests for trw_checkpoint tool."""

    def test_creates_checkpoint(self, orch_tools: dict[str, Any]) -> None:
        init_result = orch_tools["trw_init"].fn(task_name="cp-task")

        result = orch_tools["trw_checkpoint"].fn(
            run_path=init_result["run_path"],
            message="Test checkpoint",
        )
        assert result["status"] == "checkpoint_created"
        assert result["message"] == "Test checkpoint"

        cp_path = Path(init_result["run_path"]) / "meta" / "checkpoints.jsonl"
        assert cp_path.exists()
