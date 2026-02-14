"""Tests for phase input criteria (PRD-CORE-017 Step 2.2).

Validates PHASE_INPUT_CRITERIA dict, check_phase_input function,
strict vs soft mode, and the direction parameter on trw_phase_check.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.run import Phase
from trw_mcp.state.validation import (
    PHASE_INPUT_CRITERIA,
    check_phase_input,
)
from trw_mcp.tools.orchestration import register_orchestration_tools


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_rule(failures: list[Any], rule: str) -> bool:
    """Check whether any failure matches the given rule name."""
    return any(f.rule == rule for f in failures)


def _has_rule_with_severity(
    failures: list[Any], rule: str, severity: str,
) -> bool:
    """Check whether any failure matches both rule name and severity."""
    return any(f.rule == rule and f.severity == severity for f in failures)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    """Create a minimal run directory with run.yaml."""
    meta = tmp_path / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text(
        "run_id: test-001\ntask: test\nstatus: active\nphase: research\n"
        "framework: v18.0_TRW\nrun_type: implementation\n"
    )
    (tmp_path / "reports").mkdir()
    (tmp_path / "scratch" / "_orchestrator").mkdir(parents=True)
    (tmp_path / "shards").mkdir()
    return tmp_path


@pytest.fixture
def phase_check_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Register orchestration tools and return a name-to-tool mapping.

    Also bootstraps a .trw directory and sets HOME/cwd for isolation.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".trw").mkdir()

    srv = FastMCP("test")
    register_orchestration_tools(srv)
    return {t.name: t for t in srv._tool_manager._tools.values()}


# ---------------------------------------------------------------------------
# PHASE_INPUT_CRITERIA dict
# ---------------------------------------------------------------------------


class TestPhaseInputCriteriaDict:
    """PHASE_INPUT_CRITERIA covers all 6 phases."""

    def test_all_phases_covered(self) -> None:
        for phase in Phase:
            assert phase.value in PHASE_INPUT_CRITERIA

    def test_each_phase_has_criteria(self) -> None:
        for phase_name, criteria in PHASE_INPUT_CRITERIA.items():
            assert len(criteria) >= 1, f"{phase_name} has no criteria"


# ---------------------------------------------------------------------------
# check_phase_input -- per-phase tests
# ---------------------------------------------------------------------------


class TestCheckPhaseInputResearch:
    """Research phase -- minimal prerequisites."""

    def test_passes_with_run_yaml(self, run_dir: Path) -> None:
        result = check_phase_input(Phase.RESEARCH, run_dir, TRWConfig())
        assert result.valid

    def test_fails_without_run_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "meta").mkdir(parents=True)
        result = check_phase_input(Phase.RESEARCH, tmp_path, TRWConfig())
        assert not result.valid
        assert _has_rule(result.failures, "run_initialized")


class TestCheckPhaseInputPlan:
    """Plan phase -- needs research synthesis."""

    def test_warns_without_synthesis(self, run_dir: Path) -> None:
        result = check_phase_input(Phase.PLAN, run_dir, TRWConfig())
        assert result.valid
        assert _has_rule(result.failures, "research_complete")

    def test_passes_with_synthesis(self, run_dir: Path) -> None:
        synthesis = run_dir / "scratch" / "_orchestrator" / "research_synthesis.md"
        synthesis.write_text("# Research Synthesis\n")
        result = check_phase_input(Phase.PLAN, run_dir, TRWConfig())
        assert result.valid
        assert not _has_rule(result.failures, "research_complete")

    def test_strict_fails_without_synthesis(self, run_dir: Path) -> None:
        config = TRWConfig(strict_input_criteria=True)
        result = check_phase_input(Phase.PLAN, run_dir, config)
        assert not result.valid
        assert _has_rule_with_severity(result.failures, "research_complete", "error")


class TestCheckPhaseInputImplement:
    """Implement phase -- needs plan, manifest, PRDs."""

    def test_warns_without_plan(self, run_dir: Path) -> None:
        config = TRWConfig(phase_gate_enforcement="off")
        result = check_phase_input(Phase.IMPLEMENT, run_dir, config)
        assert result.valid
        assert _has_rule(result.failures, "plan_exists")
        assert _has_rule(result.failures, "manifest_exists")

    def test_passes_with_artifacts(self, run_dir: Path) -> None:
        (run_dir / "reports" / "plan.md").write_text("# Plan\n")
        (run_dir / "shards" / "manifest.yaml").write_text("waves: []\n")
        config = TRWConfig(phase_gate_enforcement="off")
        result = check_phase_input(Phase.IMPLEMENT, run_dir, config)
        assert result.valid
        assert not _has_rule(result.failures, "plan_exists")
        assert not _has_rule(result.failures, "manifest_exists")


class TestCheckPhaseInputDeliver:
    """Deliver phase -- needs reflection."""

    def _write_event(self, run_dir: Path, event_name: str) -> None:
        """Write a single event to meta/events.jsonl."""
        events_path = run_dir / "meta" / "events.jsonl"
        events_path.write_text(
            json.dumps({"event": event_name, "data": {}}) + "\n"
        )

    def test_warns_without_reflection(self, run_dir: Path) -> None:
        self._write_event(run_dir, "phase_check")
        result = check_phase_input(Phase.DELIVER, run_dir, TRWConfig())
        assert result.valid
        assert _has_rule(result.failures, "reflection_complete")

    def test_passes_with_reflection(self, run_dir: Path) -> None:
        self._write_event(run_dir, "trw_reflect_complete")
        result = check_phase_input(Phase.DELIVER, run_dir, TRWConfig())
        assert result.valid
        assert not _has_rule(result.failures, "reflection_complete")


# ---------------------------------------------------------------------------
# trw_phase_check direction parameter (integration)
# ---------------------------------------------------------------------------


class TestPhaseCheckDirection:
    """trw_phase_check direction parameter (integration test)."""

    def test_direction_enter_calls_input_check(
        self, phase_check_tools: dict[str, Any],
    ) -> None:
        """direction='enter' invokes check_phase_input instead of check_phase_exit."""
        run_path = phase_check_tools["trw_init"].fn(task_name="input-test")["run_path"]

        check_result = phase_check_tools["trw_phase_check"].fn(
            phase_name="research",
            run_path=run_path,
            direction="enter",
        )
        assert check_result["direction"] == "enter"
        assert check_result["valid"] is True

    def test_direction_exit_is_default(
        self, phase_check_tools: dict[str, Any],
    ) -> None:
        """Default direction is 'exit'."""
        run_path = phase_check_tools["trw_init"].fn(task_name="exit-test")["run_path"]

        check_result = phase_check_tools["trw_phase_check"].fn(
            phase_name="research",
            run_path=run_path,
        )
        assert check_result["direction"] == "exit"
