"""Coverage tests for primary phase exit gates."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._validation_gates_support import _make_run_dir
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.run import Phase
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.state.validation import check_phase_exit


class TestCheckPhaseExitResearch:
    """Research exit criteria: research synthesis must exist."""

    def test_research_exit_warns_when_no_synthesis(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_exit(Phase.RESEARCH, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "synthesis_exists" in rules
        synth_f = [f for f in result.failures if f.rule == "synthesis_exists"]
        assert synth_f[0].severity == "warning"
        assert result.valid is True

    def test_research_exit_passes_with_primary_synthesis(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        synthesis = run_dir / "scratch" / "_orchestrator" / "research_synthesis.md"
        synthesis.parent.mkdir(parents=True, exist_ok=True)
        synthesis.write_text("# Synthesis\nDone.", encoding="utf-8")
        config = TRWConfig()
        result = check_phase_exit(Phase.RESEARCH, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "synthesis_exists" not in rules

    def test_research_exit_passes_with_alt_synthesis(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        alt = run_dir / "reports" / "research_synthesis.md"
        alt.parent.mkdir(parents=True, exist_ok=True)
        alt.write_text("# Alt Synthesis", encoding="utf-8")
        config = TRWConfig()
        result = check_phase_exit(Phase.RESEARCH, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "synthesis_exists" not in rules


class TestCheckPhaseExitPlan:
    """Plan exit criteria: plan.md must exist, PRD enforcement checked."""

    def test_plan_exit_fails_without_plan_md(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(phase_gate_enforcement="off")
        result = check_phase_exit(Phase.PLAN, run_dir, config)
        plan_failures = [f for f in result.failures if f.rule == "plan_exists"]
        assert len(plan_failures) == 1
        assert plan_failures[0].severity == "error"
        assert result.valid is False

    def test_plan_exit_passes_with_plan_md(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        plan = run_dir / "reports" / "plan.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text("# Plan\n", encoding="utf-8")
        config = TRWConfig(phase_gate_enforcement="off")
        result = check_phase_exit(Phase.PLAN, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "plan_exists" not in rules


class TestCheckPhaseExitImplement:
    """Implement exit criteria: manifest presence, PRD enforcement, build check."""

    def test_implement_exit_warns_when_shards_exist_without_manifest(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        shards = run_dir / "shards"
        shards.mkdir(parents=True, exist_ok=True)
        config = TRWConfig(
            phase_gate_enforcement="off",
            build_check_enabled=False,
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_build_check",
            lambda *a, **kw: None,
        )
        result = check_phase_exit(Phase.IMPLEMENT, run_dir, config)
        manifest_f = [f for f in result.failures if f.rule == "manifest_exists"]
        assert len(manifest_f) == 1
        assert manifest_f[0].severity == "warning"

    def test_implement_exit_no_manifest_warning_when_no_shards_dir(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        shards = run_dir / "shards"
        if shards.exists():
            shards.rmdir()
        config = TRWConfig(
            phase_gate_enforcement="off",
            build_check_enabled=False,
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_build_check",
            lambda *a, **kw: None,
        )
        result = check_phase_exit(Phase.IMPLEMENT, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "manifest_exists" not in rules

    def test_implement_exit_invalid_prd_status_config_falls_back_to_approved(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {
                "run_id": "20260101T000000Z-test1234",
                "task": "coverage-test",
                "status": "active",
                "phase": "implement",
                "prd_scope": ["PRD-TEST-099"],
            },
        )
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)
        (prds_dir / "PRD-TEST-099.md").write_text(
            "---\nprd:\n  id: PRD-TEST-099\n  title: Test\n  version: '1.0'\n"
            "  status: review\n  priority: P1\n---\n# PRD-TEST-099\n",
            encoding="utf-8",
        )
        config = TRWConfig(
            phase_gate_enforcement="strict",
            prd_required_status_for_implement="INVALID_STATUS",
            build_check_enabled=False,
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_build_check",
            lambda *a, **kw: None,
        )
        result = check_phase_exit(Phase.IMPLEMENT, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "prd_status" in rules


class TestCheckPhaseExitValidate:
    """Validate exit criteria: advisory test info, integration/orphan/build checks."""

    def test_validate_exit_always_includes_test_advisory(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_integration_check",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_orphan_check",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_build_check",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_dry_check",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_migration_check",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates._best_effort_semantic_check",
            lambda *a, **kw: None,
        )
        config = TRWConfig()
        result = check_phase_exit(Phase.VALIDATE, run_dir, config)
        advisory = [f for f in result.failures if f.rule == "phase_test_advisory"]
        assert len(advisory) == 1
        assert advisory[0].severity == "info"
        assert result.valid is True
