"""Integration tests for phase gate input criteria checkers."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import ValidationFailure
from trw_mcp.models.run import Phase
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.state.validation.phase_gates import (
    _check_deliver_input,
    _check_implement_input,
    _check_plan_input,
    _check_review_input,
    _check_validate_input,
    check_phase_input,
)

from ._phase_gates_support import _make_run_dir, _write_events


class TestCheckImplementInput:
    """Tests for implement phase input checker."""

    def test_missing_plan_adds_failure(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_implement_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "plan_exists" in rules

    def test_missing_manifest_adds_failure(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "reports" / "plan.md").write_text("# Plan\n", encoding="utf-8")
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_implement_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "manifest_exists" in rules

    def test_all_present_no_failures(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "reports" / "plan.md").write_text("# Plan\n", encoding="utf-8")
        (run_dir / "shards" / "manifest.yaml").write_text("shards: []\n", encoding="utf-8")
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_implement_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "plan_exists" not in rules
        assert "manifest_exists" not in rules


class TestCheckValidateInput:
    """Tests for validate phase input checker."""

    def test_empty_shards_dir_adds_failure(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_validate_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "implementation_complete" in rules

    def test_no_shards_dir_adds_failure(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "shards").rmdir()
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_validate_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "implementation_complete" in rules

    def test_nonempty_shards_no_failure(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "shards" / "shard-1.yaml").write_text("shard: done\n", encoding="utf-8")
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_validate_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "implementation_complete" not in rules


class TestCheckReviewInput:
    """Tests for review phase input checker."""

    def test_no_validate_pass_event_adds_failure(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        _write_events(
            run_dir / "meta",
            [{"ts": "2026-01-01T00:00:00Z", "event": "run_init"}],
        )
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_review_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "validate_passed" in rules

    def test_no_events_no_failure(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """When there are no events, we don't know if validate passed — no failure added."""
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_review_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "validate_passed" not in rules

    def test_validate_pass_event_no_failure(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        _write_events(
            run_dir / "meta",
            [
                {
                    "ts": "2026-01-01T00:00:00Z",
                    "event": "phase_check",
                    "data": {"phase": "validate", "valid": True},
                },
            ],
        )
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_review_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "validate_passed" not in rules


class TestCheckDeliverInput:
    """Tests for deliver phase input checker."""

    def test_no_events_adds_events_exist_failure(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_deliver_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "events_exist" in rules

    def test_events_without_reflection_adds_failure(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        _write_events(
            run_dir / "meta",
            [{"ts": "2026-01-01T00:00:00Z", "event": "run_init"}],
        )
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_deliver_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "reflection_complete" in rules

    def test_events_with_reflection_no_failure(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        _write_events(
            run_dir / "meta",
            [{"ts": "2026-01-01T00:00:00Z", "event": "trw_reflect_complete"}],
        )
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_deliver_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "reflection_complete" not in rules
        assert "events_exist" not in rules


class TestCheckPlanInput:
    """Tests for plan phase input checker."""

    def test_missing_synthesis_adds_failure(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_plan_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "research_complete" in rules

    def test_synthesis_in_scratch_no_failure(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        synthesis = run_dir / "scratch" / "_orchestrator" / "research_synthesis.md"
        synthesis.write_text("# Research Synthesis\n", encoding="utf-8")
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_plan_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "research_complete" not in rules

    def test_synthesis_in_reports_no_failure(self, tmp_path: Path, writer: FileStateWriter) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "reports" / "research_synthesis.md").write_text("# Research Synthesis\n", encoding="utf-8")
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_plan_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "research_complete" not in rules


class TestCheckPhaseInputUniversalGuard:
    """Tests for the run.yaml universal guard in check_phase_input."""

    def test_missing_run_yaml_returns_invalid(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run_no_yaml"
        run_dir.mkdir()
        (run_dir / "meta").mkdir()
        result = check_phase_input(Phase.PLAN, run_dir, TRWConfig())
        assert result.valid is False
        rules = [f.rule for f in result.failures]
        assert "run_initialized" in rules
        assert result.completeness_score == 0.0

    def test_missing_run_yaml_early_return(self, tmp_path: Path) -> None:
        """Early return means no per-phase checker runs."""
        run_dir = tmp_path / "run_no_yaml2"
        run_dir.mkdir()
        (run_dir / "meta").mkdir()
        result = check_phase_input(Phase.IMPLEMENT, run_dir, TRWConfig())
        rules = [f.rule for f in result.failures]
        assert "run_initialized" in rules
        assert "plan_exists" not in rules
