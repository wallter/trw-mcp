"""Tests for trw_mcp.state._phase_validators.

Verifies extracted per-phase exit and input validator functions produce
identical results to the original monolithic check_phase_exit / check_phase_input.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import ValidationFailure
from trw_mcp.models.run import Phase
from trw_mcp.state._phase_validators import (
    PHASE_EXIT_DISPATCH,
    PHASE_INPUT_DISPATCH,
    validate_deliver_exit,
    validate_deliver_input,
    validate_implement_exit,
    validate_implement_input,
    validate_plan_exit,
    validate_plan_input,
    validate_research_exit,
    validate_research_input,
    validate_review_exit,
    validate_review_input,
    validate_validate_exit,
    validate_validate_input,
)
from trw_mcp.state.persistence import FileStateWriter

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
    writer.write_yaml(meta / "run.yaml", {
        "run_id": "20260101T000000Z-test1234",
        "task": "validator-test",
        "framework": "v24.0_TRW",
        "status": "active",
        "phase": "research",
        "confidence": "medium",
    })
    return run_dir


# ---------------------------------------------------------------------------
# Dispatch table tests
# ---------------------------------------------------------------------------

class TestDispatchTables:
    """Verify dispatch tables cover all Phase enum members."""

    def test_exit_dispatch_covers_all_phases(self) -> None:
        for phase in Phase:
            assert phase.value in PHASE_EXIT_DISPATCH, (
                f"PHASE_EXIT_DISPATCH missing key {phase.value!r}"
            )

    def test_input_dispatch_covers_all_phases(self) -> None:
        for phase in Phase:
            assert phase.value in PHASE_INPUT_DISPATCH, (
                f"PHASE_INPUT_DISPATCH missing key {phase.value!r}"
            )

    def test_exit_dispatch_maps_correct_validators(self) -> None:
        assert PHASE_EXIT_DISPATCH["research"] is validate_research_exit
        assert PHASE_EXIT_DISPATCH["plan"] is validate_plan_exit
        assert PHASE_EXIT_DISPATCH["implement"] is validate_implement_exit
        assert PHASE_EXIT_DISPATCH["validate"] is validate_validate_exit
        assert PHASE_EXIT_DISPATCH["review"] is validate_review_exit
        assert PHASE_EXIT_DISPATCH["deliver"] is validate_deliver_exit

    def test_input_dispatch_maps_correct_validators(self) -> None:
        assert PHASE_INPUT_DISPATCH["research"] is validate_research_input
        assert PHASE_INPUT_DISPATCH["plan"] is validate_plan_input
        assert PHASE_INPUT_DISPATCH["implement"] is validate_implement_input
        assert PHASE_INPUT_DISPATCH["validate"] is validate_validate_input
        assert PHASE_INPUT_DISPATCH["review"] is validate_review_input
        assert PHASE_INPUT_DISPATCH["deliver"] is validate_deliver_input


# ===================================================================
# Phase EXIT validator tests
# ===================================================================

class TestValidateResearchExit:
    """validate_research_exit produces synthesis_exists warning when missing."""

    def test_warns_without_synthesis(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        validate_research_exit(run_dir, TRWConfig(), failures)
        rules = [f.rule for f in failures]
        assert "synthesis_exists" in rules
        assert failures[0].severity == "warning"

    def test_passes_with_orchestrator_synthesis(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        synthesis = run_dir / "scratch" / "_orchestrator" / "research_synthesis.md"
        synthesis.write_text("# Synthesis\nFindings.", encoding="utf-8")
        failures: list[ValidationFailure] = []
        validate_research_exit(run_dir, TRWConfig(), failures)
        rules = [f.rule for f in failures]
        assert "synthesis_exists" not in rules

    def test_passes_with_reports_synthesis(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        alt = run_dir / "reports" / "research_synthesis.md"
        alt.write_text("# Alt\nFindings.", encoding="utf-8")
        failures: list[ValidationFailure] = []
        validate_research_exit(run_dir, TRWConfig(), failures)
        rules = [f.rule for f in failures]
        assert "synthesis_exists" not in rules


class TestValidatePlanExit:
    """validate_plan_exit checks plan.md exists and PRD enforcement."""

    def test_fails_without_plan_md(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        config = TRWConfig(phase_gate_enforcement="off")
        validate_plan_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "plan_exists" in rules
        plan_f = [f for f in failures if f.rule == "plan_exists"]
        assert plan_f[0].severity == "error"

    def test_passes_with_plan_md(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        plan = run_dir / "reports" / "plan.md"
        plan.write_text("# Plan\nContent.", encoding="utf-8")
        failures: list[ValidationFailure] = []
        config = TRWConfig(phase_gate_enforcement="off")
        validate_plan_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "plan_exists" not in rules


class TestValidateImplementExit:
    """validate_implement_exit checks manifest and PRD status."""

    def test_warns_without_manifest(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        config = TRWConfig(phase_gate_enforcement="off")
        validate_implement_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "manifest_exists" in rules

    def test_passes_with_manifest(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        manifest = run_dir / "shards" / "manifest.yaml"
        writer.write_yaml(manifest, {"waves": []})
        failures: list[ValidationFailure] = []
        config = TRWConfig(phase_gate_enforcement="off")
        validate_implement_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "manifest_exists" not in rules

    def test_no_manifest_warning_when_shards_dir_absent(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        """When shards dir does not exist, no manifest warning is produced."""
        run_dir = _make_run_dir(tmp_path, writer)
        shards = run_dir / "shards"
        shards.rmdir()
        failures: list[ValidationFailure] = []
        config = TRWConfig(phase_gate_enforcement="off")
        validate_implement_exit(run_dir, config, failures)
        rules = [f.rule for f in failures]
        assert "manifest_exists" not in rules


class TestValidateValidateExit:
    """validate_validate_exit always produces test advisory."""

    def test_includes_test_advisory(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        validate_validate_exit(run_dir, TRWConfig(), failures)
        rules = [f.rule for f in failures]
        assert "phase_test_advisory" in rules

    def test_advisory_is_info_severity(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        validate_validate_exit(run_dir, TRWConfig(), failures)
        advisory = [f for f in failures if f.rule == "phase_test_advisory"]
        assert len(advisory) == 1
        assert advisory[0].severity == "info"


class TestValidateReviewExit:
    """validate_review_exit checks final report, reflection, and quality."""

    def test_warns_without_final_report(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        validate_review_exit(run_dir, TRWConfig(), failures)
        rules = [f.rule for f in failures]
        assert "final_report_exists" in rules

    def test_warns_without_reflection_event(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(meta / "events.jsonl", {
            "event": "run_init",
            "ts": "2026-01-01T00:00:00Z",
        })
        failures: list[ValidationFailure] = []
        validate_review_exit(run_dir, TRWConfig(), failures)
        rules = [f.rule for f in failures]
        assert "reflection_required" in rules

    def test_warns_when_no_events_jsonl(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        validate_review_exit(run_dir, TRWConfig(), failures)
        reflection_f = [f for f in failures if f.rule == "reflection_required"]
        assert len(reflection_f) == 1
        assert "events.jsonl" in reflection_f[0].message

    def test_passes_with_reflection_event(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(meta / "events.jsonl", {
            "event": "reflection_complete",
            "ts": "2026-01-01T12:00:00Z",
        })
        (run_dir / "reports" / "final.md").write_text("# Final", encoding="utf-8")
        failures: list[ValidationFailure] = []
        validate_review_exit(run_dir, TRWConfig(), failures)
        rules = [f.rule for f in failures]
        assert "reflection_required" not in rules
        assert "final_report_exists" not in rules


class TestValidateDeliverExit:
    """validate_deliver_exit checks run status, sync, and integration."""

    def test_warns_incomplete_run_status(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        validate_deliver_exit(run_dir, TRWConfig(), failures)
        rules = [f.rule for f in failures]
        assert "status_complete" in rules

    def test_no_warning_when_run_complete(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        writer.write_yaml(run_dir / "meta" / "run.yaml", {
            "run_id": "20260101T000000Z-test1234",
            "task": "validator-test",
            "status": "complete",
        })
        failures: list[ValidationFailure] = []
        validate_deliver_exit(run_dir, TRWConfig(), failures)
        rules = [f.rule for f in failures]
        assert "status_complete" not in rules

    def test_includes_test_advisory(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        validate_deliver_exit(run_dir, TRWConfig(), failures)
        rules = [f.rule for f in failures]
        assert "phase_test_advisory" in rules

    def test_warns_when_sync_missing(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(meta / "events.jsonl", {
            "event": "run_init",
            "ts": "2026-01-01T00:00:00Z",
        })
        failures: list[ValidationFailure] = []
        validate_deliver_exit(run_dir, TRWConfig(), failures)
        rules = [f.rule for f in failures]
        assert "sync_required" in rules

    def test_no_sync_warning_when_synced(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(meta / "events.jsonl", {
            "event": "claude_md_synced",
            "ts": "2026-01-01T12:00:00Z",
        })
        failures: list[ValidationFailure] = []
        validate_deliver_exit(run_dir, TRWConfig(), failures)
        rules = [f.rule for f in failures]
        assert "sync_required" not in rules


# ===================================================================
# Phase INPUT validator tests
# ===================================================================

class TestValidateResearchInput:
    """validate_research_input is a no-op (no per-phase prereqs)."""

    def test_produces_no_failures(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        validate_research_input(run_dir, TRWConfig(), failures, "error")
        assert failures == []


class TestValidatePlanInput:
    """validate_plan_input requires research synthesis."""

    def test_fails_without_synthesis(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        validate_plan_input(run_dir, TRWConfig(), failures, "error")
        rules = [f.rule for f in failures]
        assert "research_complete" in rules
        assert failures[0].severity == "error"

    def test_severity_from_parameter(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        validate_plan_input(run_dir, TRWConfig(), failures, "warning")
        rc = [f for f in failures if f.rule == "research_complete"]
        assert rc[0].severity == "warning"

    def test_passes_with_orchestrator_synthesis(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        synthesis = run_dir / "scratch" / "_orchestrator" / "research_synthesis.md"
        synthesis.write_text("# Synthesis", encoding="utf-8")
        failures: list[ValidationFailure] = []
        validate_plan_input(run_dir, TRWConfig(), failures, "error")
        rules = [f.rule for f in failures]
        assert "research_complete" not in rules

    def test_passes_with_reports_synthesis(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        alt = run_dir / "reports" / "research_synthesis.md"
        alt.write_text("# Alt Synthesis", encoding="utf-8")
        failures: list[ValidationFailure] = []
        validate_plan_input(run_dir, TRWConfig(), failures, "error")
        rules = [f.rule for f in failures]
        assert "research_complete" not in rules


class TestValidateImplementInput:
    """validate_implement_input checks plan.md and manifest.yaml."""

    def test_fails_without_plan(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        config = TRWConfig(phase_gate_enforcement="off")
        validate_implement_input(run_dir, config, failures, "error")
        rules = [f.rule for f in failures]
        assert "plan_exists" in rules

    def test_fails_without_manifest(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "reports" / "plan.md").write_text("# Plan", encoding="utf-8")
        failures: list[ValidationFailure] = []
        config = TRWConfig(phase_gate_enforcement="off")
        validate_implement_input(run_dir, config, failures, "error")
        rules = [f.rule for f in failures]
        assert "manifest_exists" in rules
        assert "plan_exists" not in rules

    def test_passes_with_plan_and_manifest(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "reports" / "plan.md").write_text("# Plan", encoding="utf-8")
        writer.write_yaml(run_dir / "shards" / "manifest.yaml", {"waves": []})
        failures: list[ValidationFailure] = []
        config = TRWConfig(phase_gate_enforcement="off")
        validate_implement_input(run_dir, config, failures, "warning")
        rules = [f.rule for f in failures]
        assert "plan_exists" not in rules
        assert "manifest_exists" not in rules


class TestValidateValidateInput:
    """validate_validate_input checks shard outputs exist."""

    def test_fails_with_empty_shards(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        validate_validate_input(run_dir, TRWConfig(), failures, "error")
        rules = [f.rule for f in failures]
        assert "implementation_complete" in rules

    def test_fails_when_shards_dir_missing(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "shards").rmdir()
        failures: list[ValidationFailure] = []
        validate_validate_input(run_dir, TRWConfig(), failures, "error")
        rules = [f.rule for f in failures]
        assert "implementation_complete" in rules

    def test_passes_with_shard_files(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        writer.write_yaml(
            run_dir / "shards" / "shard-01.yaml",
            {"id": "shard-01", "status": "complete"},
        )
        failures: list[ValidationFailure] = []
        validate_validate_input(run_dir, TRWConfig(), failures, "error")
        rules = [f.rule for f in failures]
        assert "implementation_complete" not in rules


class TestValidateReviewInput:
    """validate_review_input checks validate phase gate passed."""

    def test_fails_without_validate_pass_event(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(meta / "events.jsonl", {
            "event": "run_init",
            "ts": "2026-01-01T00:00:00Z",
        })
        failures: list[ValidationFailure] = []
        validate_review_input(run_dir, TRWConfig(), failures, "error")
        rules = [f.rule for f in failures]
        assert "validate_passed" in rules

    def test_passes_with_validate_pass_event(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(meta / "events.jsonl", {
            "event": "phase_check",
            "data": {"phase": "validate", "valid": True},
        })
        failures: list[ValidationFailure] = []
        validate_review_input(run_dir, TRWConfig(), failures, "error")
        rules = [f.rule for f in failures]
        assert "validate_passed" not in rules

    def test_no_failure_when_events_empty(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        """No events.jsonl => empty events list => no failure."""
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        validate_review_input(run_dir, TRWConfig(), failures, "error")
        rules = [f.rule for f in failures]
        assert "validate_passed" not in rules


class TestValidateDeliverInput:
    """validate_deliver_input checks reflection event exists."""

    def test_fails_without_events(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        validate_deliver_input(run_dir, TRWConfig(), failures, "error")
        rules = [f.rule for f in failures]
        assert "events_exist" in rules

    def test_fails_without_reflection_event(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(meta / "events.jsonl", {
            "event": "run_init",
            "ts": "2026-01-01T00:00:00Z",
        })
        failures: list[ValidationFailure] = []
        validate_deliver_input(run_dir, TRWConfig(), failures, "error")
        rules = [f.rule for f in failures]
        assert "reflection_complete" in rules

    def test_passes_with_reflection_complete(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(meta / "events.jsonl", {
            "event": "reflection_complete",
            "ts": "2026-01-01T12:00:00Z",
        })
        failures: list[ValidationFailure] = []
        validate_deliver_input(run_dir, TRWConfig(), failures, "error")
        rules = [f.rule for f in failures]
        assert "reflection_complete" not in rules
        assert "events_exist" not in rules

    def test_passes_with_trw_reflect_complete(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(meta / "events.jsonl", {
            "event": "trw_reflect_complete",
            "ts": "2026-01-01T12:00:00Z",
        })
        failures: list[ValidationFailure] = []
        validate_deliver_input(run_dir, TRWConfig(), failures, "warning")
        rules = [f.rule for f in failures]
        assert "reflection_complete" not in rules

    def test_severity_from_parameter(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        failures: list[ValidationFailure] = []
        validate_deliver_input(run_dir, TRWConfig(), failures, "warning")
        ev = [f for f in failures if f.rule == "events_exist"]
        assert ev[0].severity == "warning"


# ===================================================================
# Integration: dispatch matches check_phase_exit / check_phase_input
# ===================================================================

class TestDispatchMatchesCheckPhaseExit:
    """Verify that check_phase_exit delegates to PHASE_EXIT_DISPATCH correctly."""

    def test_research_via_dispatch(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        from trw_mcp.state.validation import check_phase_exit

        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_exit(Phase.RESEARCH, run_dir, config)
        # Should contain synthesis_exists warning (no synthesis written)
        rules = [f.rule for f in result.failures]
        assert "synthesis_exists" in rules

    def test_plan_via_dispatch(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        from trw_mcp.state.validation import check_phase_exit

        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(phase_gate_enforcement="off")
        result = check_phase_exit(Phase.PLAN, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "plan_exists" in rules

    def test_validate_via_dispatch(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        from trw_mcp.state.validation import check_phase_exit

        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_exit(Phase.VALIDATE, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "phase_test_advisory" in rules


class TestDispatchMatchesCheckPhaseInput:
    """Verify that check_phase_input delegates to PHASE_INPUT_DISPATCH."""

    def test_plan_via_dispatch(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        from trw_mcp.state.validation import check_phase_input

        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(strict_input_criteria=True)
        result = check_phase_input(Phase.PLAN, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "research_complete" in rules

    def test_deliver_via_dispatch(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        from trw_mcp.state.validation import check_phase_input

        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(strict_input_criteria=True)
        result = check_phase_input(Phase.DELIVER, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "events_exist" in rules

    def test_research_via_dispatch_no_per_phase_failures(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        from trw_mcp.state.validation import check_phase_input

        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_input(Phase.RESEARCH, run_dir, config)
        error_failures = [f for f in result.failures if f.severity == "error"]
        assert len(error_failures) == 0
