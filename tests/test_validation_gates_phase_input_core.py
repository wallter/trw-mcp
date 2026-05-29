"""Coverage tests for validation phase input gates."""

from __future__ import annotations

from pathlib import Path

from tests._validation_gates_support import _make_run_dir
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.run import Phase
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.state.validation import check_phase_input


class TestCheckPhaseInputNoRunYaml:
    """check_phase_input returns valid=False when run.yaml is absent."""

    def test_missing_run_yaml_returns_invalid(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "no_run"
        run_dir.mkdir()
        config = TRWConfig()
        result = check_phase_input(Phase.RESEARCH, run_dir, config)
        assert result.valid is False
        rules = [f.rule for f in result.failures]
        assert "run_initialized" in rules

    def test_completeness_score_is_zero_when_no_run_yaml(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "no_run2"
        run_dir.mkdir()
        config = TRWConfig()
        result = check_phase_input(Phase.PLAN, run_dir, config)
        assert result.completeness_score == 0.0


class TestCheckPhaseInputResearch:
    """Research phase has no per-phase prerequisites beyond run.yaml."""

    def test_research_phase_passes_with_run_yaml(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_input(Phase.RESEARCH, run_dir, config)
        error_failures = [f for f in result.failures if f.severity == "error"]
        assert len(error_failures) == 0


class TestCheckPhaseInputPlan:
    """Plan phase requires research synthesis."""

    def test_plan_fails_without_synthesis(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(strict_input_criteria=True)
        result = check_phase_input(Phase.PLAN, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "research_complete" in rules

    def test_plan_passes_with_orchestrator_synthesis(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        synthesis = run_dir / "scratch" / "_orchestrator" / "research_synthesis.md"
        synthesis.write_text("# Synthesis\nFindings here.", encoding="utf-8")
        config = TRWConfig()
        result = check_phase_input(Phase.PLAN, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "research_complete" not in rules

    def test_plan_passes_with_reports_synthesis(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        alt = run_dir / "reports" / "research_synthesis.md"
        alt.write_text("# Alt Synthesis\nFindings.", encoding="utf-8")
        config = TRWConfig()
        result = check_phase_input(Phase.PLAN, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "research_complete" not in rules


class TestCheckPhaseInputImplement:
    """Implement phase requires plan.md and manifest.yaml."""

    def test_implement_fails_without_plan(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(
            strict_input_criteria=True,
            phase_gate_enforcement="off",
        )
        result = check_phase_input(Phase.IMPLEMENT, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "plan_exists" in rules

    def test_implement_fails_without_manifest(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        plan = run_dir / "reports" / "plan.md"
        plan.write_text("# Plan\n", encoding="utf-8")
        config = TRWConfig(
            strict_input_criteria=True,
            phase_gate_enforcement="off",
        )
        result = check_phase_input(Phase.IMPLEMENT, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "manifest_exists" in rules
        assert "plan_exists" not in rules

    def test_implement_passes_with_plan_and_manifest(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        plan = run_dir / "reports" / "plan.md"
        plan.write_text("# Plan\nContent.", encoding="utf-8")
        manifest = run_dir / "shards" / "manifest.yaml"
        writer.write_yaml(manifest, {"waves": []})
        config = TRWConfig(phase_gate_enforcement="off")
        result = check_phase_input(Phase.IMPLEMENT, run_dir, config)
        error_failures = [f for f in result.failures if f.severity == "error"]
        assert len(error_failures) == 0


class TestCheckPhaseInputValidate:
    """Validate phase requires shard outputs to exist."""

    def test_validate_fails_with_empty_shards(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(strict_input_criteria=True)
        result = check_phase_input(Phase.VALIDATE, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "implementation_complete" in rules

    def test_validate_fails_when_shards_dir_missing(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        shards = run_dir / "shards"
        shards.rmdir()
        config = TRWConfig(strict_input_criteria=True)
        result = check_phase_input(Phase.VALIDATE, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "implementation_complete" in rules

    def test_validate_passes_with_shard_files(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        shard_file = run_dir / "shards" / "shard-01.yaml"
        writer.write_yaml(shard_file, {"id": "shard-01", "status": "complete"})
        config = TRWConfig()
        result = check_phase_input(Phase.VALIDATE, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "implementation_complete" not in rules


class TestCheckPhaseInputReview:
    """Review phase requires a validate_passed phase_check event."""

    def test_review_fails_without_validate_pass_event(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(
            meta / "events.jsonl",
            {
                "event": "run_init",
                "ts": "2026-01-01T00:00:00Z",
            },
        )
        config = TRWConfig(strict_input_criteria=True)
        result = check_phase_input(Phase.REVIEW, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "validate_passed" in rules

    def test_review_passes_with_validate_pass_event(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(
            meta / "events.jsonl",
            {
                "event": "phase_check",
                "data": {"phase": "validate", "valid": True},
            },
        )
        config = TRWConfig()
        result = check_phase_input(Phase.REVIEW, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "validate_passed" not in rules

    def test_review_no_failure_when_events_empty(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_input(Phase.REVIEW, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "validate_passed" not in rules


class TestCheckPhaseInputDeliver:
    """Deliver phase requires reflection event in events.jsonl."""

    def test_deliver_fails_without_events(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(strict_input_criteria=True)
        result = check_phase_input(Phase.DELIVER, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "events_exist" in rules

    def test_deliver_fails_without_reflection_event(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(
            meta / "events.jsonl",
            {
                "event": "run_init",
                "ts": "2026-01-01T00:00:00Z",
            },
        )
        config = TRWConfig(strict_input_criteria=True)
        result = check_phase_input(Phase.DELIVER, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "reflection_complete" in rules

    def test_deliver_passes_with_reflection_complete_event(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(
            meta / "events.jsonl",
            {
                "event": "reflection_complete",
                "ts": "2026-01-01T12:00:00Z",
            },
        )
        config = TRWConfig()
        result = check_phase_input(Phase.DELIVER, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "reflection_complete" not in rules
        assert "events_exist" not in rules

    def test_deliver_passes_with_trw_reflect_complete_event(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(
            meta / "events.jsonl",
            {
                "event": "trw_reflect_complete",
                "ts": "2026-01-01T12:00:00Z",
            },
        )
        config = TRWConfig()
        result = check_phase_input(Phase.DELIVER, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "reflection_complete" not in rules

    def test_deliver_severity_warning_in_lenient_mode(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(
            meta / "events.jsonl",
            {
                "event": "run_init",
                "ts": "2026-01-01T00:00:00Z",
            },
        )
        config = TRWConfig(strict_input_criteria=False)
        result = check_phase_input(Phase.DELIVER, run_dir, config)
        reflection_failures = [f for f in result.failures if f.rule == "reflection_complete"]
        assert len(reflection_failures) == 1
        assert reflection_failures[0].severity == "warning"
        assert result.valid is True
