"""Integration tests for phase gate dispatcher coverage."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.run import Phase
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.state.validation.phase_gates import check_phase_exit

from ._phase_gates_support import _make_run_dir


class TestCheckPhaseExitDispatch:
    """Verify check_phase_exit dispatches to all per-phase checkers."""

    def test_implement_exit(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(build_check_enabled=False)
        result = check_phase_exit(Phase.IMPLEMENT, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "manifest_exists" in rules

    def test_validate_exit(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(build_check_enabled=False)
        result = check_phase_exit(Phase.VALIDATE, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "phase_test_advisory" in rules

    def test_review_exit(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_exit(Phase.REVIEW, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "final_report_exists" in rules

    def test_deliver_exit(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(build_check_enabled=False)
        result = check_phase_exit(Phase.DELIVER, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "status_complete" in rules

    def test_unknown_phase_no_checker(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Phases without a registered checker return no failures from checker."""
        run_dir = _make_run_dir(tmp_path, writer)
        result = check_phase_exit(Phase.RESEARCH, run_dir, TRWConfig())
        rules = [f.rule for f in result.failures]
        assert "synthesis_exists" in rules
