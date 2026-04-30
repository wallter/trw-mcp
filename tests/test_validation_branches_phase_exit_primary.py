"""Extra coverage tests for trw_mcp/state/validation.py."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.run import Phase
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.state.validation import check_phase_exit

from tests._validation_branches_support import _make_run_dir


class TestCheckPhaseExitResearch:
    """check_phase_exit for RESEARCH phase."""

    def test_research_exit_warns_without_synthesis(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_exit(Phase.RESEARCH, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "synthesis_exists" in rules

    def test_research_exit_passes_with_orchestrator_synthesis(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        synthesis = run_dir / "scratch" / "_orchestrator" / "research_synthesis.md"
        synthesis.write_text("# Synthesis\nFindings.", encoding="utf-8")
        config = TRWConfig()
        result = check_phase_exit(Phase.RESEARCH, run_dir, config)
        assert not any(f.rule == "synthesis_exists" for f in result.failures)

    def test_research_exit_passes_with_reports_synthesis(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        alt_path = run_dir / "reports" / "research_synthesis.md"
        alt_path.write_text("# Alt\nContent.", encoding="utf-8")
        config = TRWConfig()
        result = check_phase_exit(Phase.RESEARCH, run_dir, config)
        assert not any(f.rule == "synthesis_exists" for f in result.failures)


class TestCheckPhaseExitPlan:
    """check_phase_exit for PLAN phase."""

    def test_plan_exit_fails_without_plan_md(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(phase_gate_enforcement="off")
        result = check_phase_exit(Phase.PLAN, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "plan_exists" in rules

    def test_plan_exit_passes_with_plan_md(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "reports" / "plan.md").write_text("# Plan\n", encoding="utf-8")
        config = TRWConfig(phase_gate_enforcement="off")
        result = check_phase_exit(Phase.PLAN, run_dir, config)
        assert not any(f.rule == "plan_exists" for f in result.failures)


class TestCheckPhaseExitImplement:
    """check_phase_exit for IMPLEMENT phase."""

    def test_implement_exit_warns_without_manifest(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(phase_gate_enforcement="off")
        result = check_phase_exit(Phase.IMPLEMENT, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "manifest_exists" in rules

    def test_implement_exit_passes_with_manifest(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        manifest = run_dir / "shards" / "manifest.yaml"
        writer.write_yaml(manifest, {"waves": []})
        config = TRWConfig(phase_gate_enforcement="off")
        result = check_phase_exit(Phase.IMPLEMENT, run_dir, config)
        assert not any(f.rule == "manifest_exists" for f in result.failures)


class TestCheckPhaseExitValidate:
    """check_phase_exit for VALIDATE phase."""

    def test_validate_exit_includes_test_advisory(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_exit(Phase.VALIDATE, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "phase_test_advisory" in rules

    def test_validate_exit_advisory_is_info(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_exit(Phase.VALIDATE, run_dir, config)
        advisories = [f for f in result.failures if f.rule == "phase_test_advisory"]
        assert all(a.severity == "info" for a in advisories)
