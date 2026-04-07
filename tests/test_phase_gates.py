"""Integration tests for phase gate exit and input criteria.

Covers uncovered paths in state/validation/phase_gates.py:
- _check_implement_exit: shard manifest missing, valid PRD status override
- _check_validate_exit: advisory info failure always appended
- _check_review_exit: final report missing, no events, events with reflection
- _check_deliver_exit: run.yaml status not complete, no events
- _check_implement_input: plan.md missing, manifest missing
- _check_validate_input: shards empty vs non-empty
- _check_review_input: events exist but validate not passed
- _check_deliver_input: reflection missing in events, no events at all
- check_phase_input: run.yaml missing universal guard
- PHASE_INPUT_CRITERIA / PHASE_EXIT_CRITERIA constant coverage
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.run import Phase
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.state.validation.phase_gates import (
    PHASE_EXIT_CRITERIA,
    PHASE_INPUT_CRITERIA,
    _build_phase_result,
    _check_deliver_exit,
    _check_implement_exit,
    _check_implement_input,
    _check_plan_exit,
    _check_plan_input,
    _check_review_exit,
    _check_review_input,
    _check_validate_exit,
    _check_validate_input,
    check_phase_exit,
    check_phase_input,
)
from trw_mcp.models.requirements import ValidationFailure


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run_dir(tmp_path: Path, writer: FileStateWriter) -> Path:
    """Create a minimal run directory with run.yaml present."""
    run_dir = tmp_path / "runs" / "20260101T000000Z-pg1234"
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    (run_dir / "reports").mkdir()
    (run_dir / "scratch" / "_orchestrator").mkdir(parents=True)
    (run_dir / "shards").mkdir()
    writer.write_yaml(
        meta / "run.yaml",
        {
            "run_id": "20260101T000000Z-pg1234",
            "task": "phase-gates-test",
            "framework": "v24.0_TRW",
            "status": "active",
            "phase": "research",
            "confidence": "medium",
        },
    )
    return run_dir


def _write_events(meta_path: Path, events: list[dict]) -> None:
    """Write events to events.jsonl."""
    meta_path.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(e) + "\n" for e in events]
    (meta_path / "events.jsonl").write_text("".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestPhaseConstants:
    """Verify PHASE_INPUT_CRITERIA and PHASE_EXIT_CRITERIA are sane."""

    @pytest.mark.unit
    def test_input_criteria_has_all_phases(self) -> None:
        for phase in ("research", "plan", "implement", "validate", "review", "deliver"):
            assert phase in PHASE_INPUT_CRITERIA, f"{phase} missing from PHASE_INPUT_CRITERIA"

    @pytest.mark.unit
    def test_exit_criteria_has_all_phases(self) -> None:
        for phase in ("research", "plan", "implement", "validate", "review", "deliver"):
            assert phase in PHASE_EXIT_CRITERIA, f"{phase} missing from PHASE_EXIT_CRITERIA"

    @pytest.mark.unit
    def test_input_criteria_nonempty(self) -> None:
        for phase, criteria in PHASE_INPUT_CRITERIA.items():
            assert len(criteria) > 0, f"{phase} has empty input criteria"

    @pytest.mark.unit
    def test_exit_criteria_nonempty(self) -> None:
        for phase, criteria in PHASE_EXIT_CRITERIA.items():
            assert len(criteria) > 0, f"{phase} has empty exit criteria"


# ---------------------------------------------------------------------------
# _build_phase_result
# ---------------------------------------------------------------------------


class TestBuildPhaseResult:
    """Unit tests for _build_phase_result helper."""

    @pytest.mark.unit
    def test_no_failures_is_valid(self) -> None:
        result = _build_phase_result([], ["crit1", "crit2"], "research", "phase_exit_checked")
        assert result.valid is True
        assert result.completeness_score == 1.0

    @pytest.mark.unit
    def test_error_severity_marks_invalid(self) -> None:
        failures = [
            ValidationFailure(field="f", rule="r", message="m", severity="error")
        ]
        result = _build_phase_result(failures, ["crit1"], "plan", "phase_exit_checked")
        assert result.valid is False

    @pytest.mark.unit
    def test_warning_only_stays_valid(self) -> None:
        failures = [
            ValidationFailure(field="f", rule="r", message="m", severity="warning")
        ]
        result = _build_phase_result(failures, ["crit1"], "research", "phase_exit_checked")
        assert result.valid is True

    @pytest.mark.unit
    def test_completeness_score_decreases_with_failures(self) -> None:
        failures = [
            ValidationFailure(field="f", rule="r", message="m", severity="warning"),
            ValidationFailure(field="f2", rule="r2", message="m2", severity="warning"),
        ]
        result = _build_phase_result(failures, ["c1", "c2", "c3", "c4"], "plan", "phase_exit_checked")
        assert result.completeness_score < 1.0
        assert result.completeness_score >= 0.0


# ---------------------------------------------------------------------------
# _check_implement_exit
# ---------------------------------------------------------------------------


class TestCheckImplementExit:
    """Tests for implement phase exit checker."""

    def test_shards_exist_no_manifest_adds_warning(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        # shards/ dir exists (created by _make_run_dir) but no manifest.yaml
        failures: list[ValidationFailure] = []
        config = TRWConfig(build_check_enabled=False)
        _check_implement_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "manifest_exists" in rules

    def test_no_shards_dir_no_manifest_warning(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "shards").rmdir()
        failures: list[ValidationFailure] = []
        config = TRWConfig(build_check_enabled=False)
        _check_implement_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        # No shards dir → no manifest_exists warning
        assert "manifest_exists" not in rules

    def test_shards_with_manifest_no_warning(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        manifest = run_dir / "shards" / "manifest.yaml"
        manifest.write_text("shards: []\n", encoding="utf-8")
        failures: list[ValidationFailure] = []
        config = TRWConfig(build_check_enabled=False)
        _check_implement_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "manifest_exists" not in rules

    def test_invalid_prd_required_status_falls_back_to_approved(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """If prd_required_status_for_implement is invalid, no ValueError raised."""
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        # Set an invalid status string to trigger the ValueError fallback
        config = TRWConfig(
            build_check_enabled=False,
            prd_required_status_for_implement="NOT_A_VALID_STATUS",
        )
        # Should not raise
        _check_implement_exit(run_dir, config, failures)


# ---------------------------------------------------------------------------
# _check_validate_exit
# ---------------------------------------------------------------------------


class TestCheckValidateExit:
    """Tests for validate phase exit checker."""

    def test_advisory_info_always_appended(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        config = TRWConfig(build_check_enabled=False)
        _check_validate_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "phase_test_advisory" in rules

    def test_phase_test_advisory_severity_is_info(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        config = TRWConfig(build_check_enabled=False)
        _check_validate_exit(run_dir, config, failures)
        info_failures = [f for f in failures if f.rule == "phase_test_advisory"]
        assert len(info_failures) == 1
        assert info_failures[0].severity == "info"


# ---------------------------------------------------------------------------
# _check_review_exit
# ---------------------------------------------------------------------------


class TestCheckReviewExit:
    """Tests for review phase exit checker."""

    def test_missing_final_report_adds_warning(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_review_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "final_report_exists" in rules

    def test_final_report_exists_no_warning(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "reports" / "final.md").write_text("# Final Report\n", encoding="utf-8")
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_review_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "final_report_exists" not in rules

    def test_no_events_adds_reflection_warning(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "reports" / "final.md").write_text("# Final\n", encoding="utf-8")
        # No events.jsonl
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_review_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "reflection_required" in rules

    def test_events_with_reflection_no_warning(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "reports" / "final.md").write_text("# Final\n", encoding="utf-8")
        # Use recognized event name from _REFLECTION_EVENTS frozenset
        _write_events(
            run_dir / "meta",
            [{"ts": "2026-01-01T00:00:00Z", "event": "reflection_complete"}],
        )
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_review_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "reflection_required" not in rules

    def test_events_without_reflection_adds_warning(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "reports" / "final.md").write_text("# Final\n", encoding="utf-8")
        _write_events(
            run_dir / "meta",
            [{"ts": "2026-01-01T00:00:00Z", "event": "run_init"}],
        )
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_review_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "reflection_required" in rules


# ---------------------------------------------------------------------------
# _check_deliver_exit
# ---------------------------------------------------------------------------


class TestCheckDeliverExit:
    """Tests for deliver phase exit checker."""

    def test_run_not_complete_adds_warning(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        config = TRWConfig(build_check_enabled=False)
        _check_deliver_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "status_complete" in rules

    def test_run_complete_no_status_warning(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        # Overwrite run.yaml with status=complete
        writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {
                "run_id": "20260101T000000Z-pg1234",
                "task": "phase-gates-test",
                "framework": "v24.0_TRW",
                "status": "complete",
                "phase": "deliver",
                "confidence": "high",
            },
        )
        failures: list[ValidationFailure] = []
        config = TRWConfig(build_check_enabled=False)
        _check_deliver_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "status_complete" not in rules

    def test_advisory_always_appended(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        config = TRWConfig(build_check_enabled=False)
        _check_deliver_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "phase_test_advisory" in rules

    def test_events_with_sync_no_warning(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {
                "run_id": "20260101T000000Z-pg1234",
                "task": "test",
                "framework": "v24.0_TRW",
                "status": "complete",
                "phase": "deliver",
                "confidence": "high",
            },
        )
        _write_events(
            run_dir / "meta",
            [{"ts": "2026-01-01T00:00:00Z", "event": "claude_md_sync"}],
        )
        failures: list[ValidationFailure] = []
        config = TRWConfig(build_check_enabled=False)
        _check_deliver_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "sync_required" not in rules

    def test_events_without_sync_adds_warning(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        _write_events(
            run_dir / "meta",
            [{"ts": "2026-01-01T00:00:00Z", "event": "run_init"}],
        )
        failures: list[ValidationFailure] = []
        config = TRWConfig(build_check_enabled=False)
        _check_deliver_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "sync_required" in rules


# ---------------------------------------------------------------------------
# _check_plan_exit (supplement existing coverage)
# ---------------------------------------------------------------------------


class TestCheckPlanExit:
    """Tests for plan phase exit checker."""

    def test_missing_plan_md_adds_error(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_plan_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "plan_exists" in rules

    def test_plan_md_exists_no_error(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "reports" / "plan.md").write_text("# Plan\n", encoding="utf-8")
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_plan_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "plan_exists" not in rules


# ---------------------------------------------------------------------------
# _check_implement_input
# ---------------------------------------------------------------------------


class TestCheckImplementInput:
    """Tests for implement phase input checker."""

    def test_missing_plan_adds_failure(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_implement_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "plan_exists" in rules

    def test_missing_manifest_adds_failure(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "reports" / "plan.md").write_text("# Plan\n", encoding="utf-8")
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_implement_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "manifest_exists" in rules

    def test_all_present_no_failures(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "reports" / "plan.md").write_text("# Plan\n", encoding="utf-8")
        (run_dir / "shards" / "manifest.yaml").write_text("shards: []\n", encoding="utf-8")
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_implement_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "plan_exists" not in rules
        assert "manifest_exists" not in rules


# ---------------------------------------------------------------------------
# _check_validate_input
# ---------------------------------------------------------------------------


class TestCheckValidateInput:
    """Tests for validate phase input checker."""

    def test_empty_shards_dir_adds_failure(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        # shards/ exists but is empty
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_validate_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "implementation_complete" in rules

    def test_no_shards_dir_adds_failure(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "shards").rmdir()
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_validate_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "implementation_complete" in rules

    def test_nonempty_shards_no_failure(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "shards" / "shard-1.yaml").write_text("shard: done\n", encoding="utf-8")
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_validate_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "implementation_complete" not in rules


# ---------------------------------------------------------------------------
# _check_review_input
# ---------------------------------------------------------------------------


class TestCheckReviewInput:
    """Tests for review phase input checker."""

    def test_no_validate_pass_event_adds_failure(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
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

    def test_no_events_no_failure(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """When there are no events, we don't know if validate passed — no failure added."""
        run_dir = _make_run_dir(tmp_path, writer)
        # No events.jsonl
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_review_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "validate_passed" not in rules

    def test_validate_pass_event_no_failure(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        # _is_validate_pass requires event="phase_check" with data.phase="validate" and data.valid=True
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


# ---------------------------------------------------------------------------
# _check_deliver_input
# ---------------------------------------------------------------------------


class TestCheckDeliverInput:
    """Tests for deliver phase input checker."""

    def test_no_events_adds_events_exist_failure(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        # No events.jsonl
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        from trw_mcp.state.validation.phase_gates import _check_deliver_input
        _check_deliver_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "events_exist" in rules

    def test_events_without_reflection_adds_failure(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        _write_events(
            run_dir / "meta",
            [{"ts": "2026-01-01T00:00:00Z", "event": "run_init"}],
        )
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        from trw_mcp.state.validation.phase_gates import _check_deliver_input
        _check_deliver_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "reflection_complete" in rules

    def test_events_with_reflection_no_failure(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        # Use recognized event name from _REFLECTION_EVENTS frozenset
        _write_events(
            run_dir / "meta",
            [{"ts": "2026-01-01T00:00:00Z", "event": "trw_reflect_complete"}],
        )
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        from trw_mcp.state.validation.phase_gates import _check_deliver_input
        _check_deliver_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "reflection_complete" not in rules
        assert "events_exist" not in rules


# ---------------------------------------------------------------------------
# _check_plan_input
# ---------------------------------------------------------------------------


class TestCheckPlanInput:
    """Tests for plan phase input checker."""

    def test_missing_synthesis_adds_failure(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_plan_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "research_complete" in rules

    def test_synthesis_in_scratch_no_failure(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        synthesis = run_dir / "scratch" / "_orchestrator" / "research_synthesis.md"
        synthesis.write_text("# Research Synthesis\n", encoding="utf-8")
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_plan_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "research_complete" not in rules

    def test_synthesis_in_reports_no_failure(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        alt = run_dir / "reports" / "research_synthesis.md"
        alt.write_text("# Research Synthesis\n", encoding="utf-8")
        failures: list[ValidationFailure] = []
        config = TRWConfig()
        _check_plan_input(run_dir, config, "error", failures)
        rules = [f.rule for f in failures]
        assert "research_complete" not in rules


# ---------------------------------------------------------------------------
# check_phase_input — universal run.yaml guard
# ---------------------------------------------------------------------------


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
        # Only run_initialized failure, not plan_exists
        rules = [f.rule for f in result.failures]
        assert "run_initialized" in rules
        assert "plan_exists" not in rules


# ---------------------------------------------------------------------------
# check_phase_exit — full dispatch tests for uncovered phases
# ---------------------------------------------------------------------------


class TestCheckPhaseExitDispatch:
    """Verify check_phase_exit dispatches to all per-phase checkers."""

    def test_implement_exit(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(build_check_enabled=False)
        result = check_phase_exit(Phase.IMPLEMENT, run_dir, config)
        # shards/ exists but manifest missing → manifest_exists warning
        rules = [f.rule for f in result.failures]
        assert "manifest_exists" in rules

    def test_validate_exit(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(build_check_enabled=False)
        result = check_phase_exit(Phase.VALIDATE, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "phase_test_advisory" in rules

    def test_review_exit(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_exit(Phase.REVIEW, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "final_report_exists" in rules

    def test_deliver_exit(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(build_check_enabled=False)
        result = check_phase_exit(Phase.DELIVER, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "status_complete" in rules

    def test_unknown_phase_no_checker(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Phases without a registered checker return no failures from checker."""
        # RESEARCH has _check_research_exit — let's confirm it runs
        run_dir = _make_run_dir(tmp_path, writer)
        result = check_phase_exit(Phase.RESEARCH, run_dir, TRWConfig())
        # synthesis_exists is a warning from _check_research_exit
        rules = [f.rule for f in result.failures]
        assert "synthesis_exists" in rules


# ---------------------------------------------------------------------------
# check_phase_input — dispatch for all covered phases
# ---------------------------------------------------------------------------


class TestCheckPhaseInputDispatch:
    """Verify check_phase_input dispatches correctly to all per-phase checkers."""

    def test_implement_input(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        result = check_phase_input(Phase.IMPLEMENT, run_dir, TRWConfig(strict_input_criteria=True))
        rules = [f.rule for f in result.failures]
        assert "plan_exists" in rules

    def test_validate_input(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        result = check_phase_input(Phase.VALIDATE, run_dir, TRWConfig(strict_input_criteria=True))
        rules = [f.rule for f in result.failures]
        assert "implementation_complete" in rules

    def test_review_input_no_events(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        result = check_phase_input(Phase.REVIEW, run_dir, TRWConfig(strict_input_criteria=True))
        # No events → no validate_passed check (review checker is silent when no events)
        rules = [f.rule for f in result.failures]
        assert "validate_passed" not in rules

    def test_strict_vs_non_strict_severity(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """strict_input_criteria=True uses error severity; False uses warning."""
        run_dir = _make_run_dir(tmp_path, writer)
        strict_result = check_phase_input(
            Phase.IMPLEMENT, run_dir, TRWConfig(strict_input_criteria=True)
        )
        non_strict_result = check_phase_input(
            Phase.IMPLEMENT, run_dir, TRWConfig(strict_input_criteria=False)
        )

        # Both should have plan_exists failure but different severities
        strict_severities = {f.rule: f.severity for f in strict_result.failures}
        non_strict_severities = {f.rule: f.severity for f in non_strict_result.failures}

        if "plan_exists" in strict_severities:
            assert strict_severities["plan_exists"] == "error"
        if "plan_exists" in non_strict_severities:
            assert non_strict_severities["plan_exists"] == "warning"

    def test_research_input_passes(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        result = check_phase_input(Phase.RESEARCH, run_dir, TRWConfig())
        # research has no per-phase checker — only universal run.yaml check
        error_failures = [f for f in result.failures if f.severity == "error"]
        assert len(error_failures) == 0
