"""Integration tests for phase validation dispatch.

Verifies that check_phase_exit and check_phase_input correctly dispatch
to per-phase validator functions via the production code path.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.run import Phase
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.state.validation import check_phase_exit, check_phase_input

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run_dir(tmp_path: Path, writer: FileStateWriter) -> Path:
    """Create a minimal run directory with run.yaml present."""
    run_dir = tmp_path / "runs" / "20260101T000000Z-test1234"
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    (run_dir / "reports").mkdir()
    (run_dir / "scratch" / "_orchestrator").mkdir(parents=True)
    (run_dir / "shards").mkdir()
    writer.write_yaml(
        meta / "run.yaml",
        {
            "run_id": "20260101T000000Z-test1234",
            "task": "validator-test",
            "framework": "v24.0_TRW",
            "status": "active",
            "phase": "research",
            "confidence": "medium",
        },
    )
    return run_dir


# ---------------------------------------------------------------------------
# Exit dispatch integration tests
# ---------------------------------------------------------------------------


class TestPhaseExitDispatch:
    """Verify that check_phase_exit delegates to per-phase validators."""

    def test_research_exit(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        result = check_phase_exit(Phase.RESEARCH, run_dir, TRWConfig())
        rules = [f.rule for f in result.failures]
        assert "synthesis_exists" in rules

    def test_plan_exit(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        result = check_phase_exit(Phase.PLAN, run_dir, TRWConfig(phase_gate_enforcement="off"))
        rules = [f.rule for f in result.failures]
        assert "plan_exists" in rules

    def test_validate_exit(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        result = check_phase_exit(Phase.VALIDATE, run_dir, TRWConfig())
        rules = [f.rule for f in result.failures]
        assert "phase_test_advisory" in rules


# ---------------------------------------------------------------------------
# Input dispatch integration tests
# ---------------------------------------------------------------------------


class TestPhaseInputDispatch:
    """Verify that check_phase_input delegates to per-phase validators."""

    def test_plan_input(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        result = check_phase_input(Phase.PLAN, run_dir, TRWConfig(strict_input_criteria=True))
        rules = [f.rule for f in result.failures]
        assert "research_complete" in rules

    def test_deliver_input(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        result = check_phase_input(Phase.DELIVER, run_dir, TRWConfig(strict_input_criteria=True))
        rules = [f.rule for f in result.failures]
        assert "events_exist" in rules

    def test_research_input_no_errors(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        result = check_phase_input(Phase.RESEARCH, run_dir, TRWConfig())
        error_failures = [f for f in result.failures if f.severity == "error"]
        assert len(error_failures) == 0
