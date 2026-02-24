"""Extra coverage tests for trw_mcp/state/validation.py.

Targets uncovered branches identified by coverage report:
- Lines 206-211: _is_validate_pass
- Lines 312-359: FileContractValidator.validate_contract
- Lines 382-428: validate_wave_contracts
- Lines 492-540: _check_prd_enforcement inner loop (PRD status checking)
- Lines 566-678: _check_build_status
- Lines 693-736: _best_effort_build_check + _best_effort_integration_check
- Lines 754-955: check_phase_exit (all phases)
- Lines 1006-1121: check_phase_input (uncovered branches)
- Lines 1703-1761: validate_prd_quality_v2 exception branches + v1_result path
- Lines 1851-1966: auto_progress_prds
- Lines 1987-2043: check_integration edge cases
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import PRDStatus, ValidationFailure
from trw_mcp.models.run import OutputContract, Phase, ShardCard, ShardStatus, WaveEntry
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.state.validation import (
    FileContractValidator,
    _best_effort_build_check,
    _best_effort_integration_check,
    _check_build_status,
    _is_validate_pass,
    auto_progress_prds,
    check_integration,
    check_phase_exit,
    check_phase_input,
    validate_prd_quality_v2,
    validate_wave_contracts,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_run_dir(tmp_path: Path, writer: FileStateWriter) -> Path:
    """Create a minimal run directory with run.yaml present."""
    run_dir = tmp_path / "runs" / "20260101T000000Z-extra1234"
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    (run_dir / "reports").mkdir()
    (run_dir / "scratch" / "_orchestrator").mkdir(parents=True)
    (run_dir / "shards").mkdir()
    writer.write_yaml(meta / "run.yaml", {
        "run_id": "20260101T000000Z-extra1234",
        "task": "extra-coverage-test",
        "framework": "v24.0_TRW",
        "status": "active",
        "phase": "research",
        "confidence": "medium",
    })
    return run_dir


def _make_prd_file(
    prds_dir: Path,
    prd_id: str,
    status: str = "approved",
) -> Path:
    """Write a minimal PRD markdown file."""
    content = f"""\
---
prd:
  id: {prd_id}
  title: Test PRD
  version: "1.0"
  status: {status}
  priority: P1
---

# {prd_id}
"""
    prd_file = prds_dir / f"{prd_id}.md"
    prd_file.write_text(content, encoding="utf-8")
    return prd_file


# ---------------------------------------------------------------------------
# _is_validate_pass (lines 206-211)
# ---------------------------------------------------------------------------

class TestIsValidatePass:
    """Unit tests for _is_validate_pass predicate."""

    def test_returns_true_for_phase_check_validate_valid(self) -> None:
        event = {"event": "phase_check", "data": {"phase": "validate", "valid": True}}
        assert _is_validate_pass(event) is True

    def test_returns_false_for_wrong_event_name(self) -> None:
        event = {"event": "run_init", "data": {"phase": "validate", "valid": True}}
        assert _is_validate_pass(event) is False

    def test_returns_false_when_data_not_dict(self) -> None:
        event = {"event": "phase_check", "data": "not_a_dict"}
        assert _is_validate_pass(event) is False

    def test_returns_false_when_data_is_none(self) -> None:
        event: dict[str, object] = {"event": "phase_check", "data": None}
        assert _is_validate_pass(event) is False

    def test_returns_false_for_different_phase(self) -> None:
        event = {"event": "phase_check", "data": {"phase": "implement", "valid": True}}
        assert _is_validate_pass(event) is False

    def test_returns_false_when_valid_is_false(self) -> None:
        event = {"event": "phase_check", "data": {"phase": "validate", "valid": False}}
        assert _is_validate_pass(event) is False

    def test_returns_false_when_no_data_key(self) -> None:
        event: dict[str, object] = {"event": "phase_check"}
        assert _is_validate_pass(event) is False


# ---------------------------------------------------------------------------
# FileContractValidator (lines 312-359)
# ---------------------------------------------------------------------------

class TestFileContractValidator:
    """Tests for FileContractValidator.validate_contract."""

    def test_required_file_missing_returns_failure(self, tmp_path: Path) -> None:
        contract = OutputContract(
            file="missing_file.yaml",
            required=True,
            schema_keys=[],
        )
        validator = FileContractValidator()
        failures = validator.validate_contract(contract, tmp_path)
        assert len(failures) == 1
        assert failures[0].rule == "file_exists"
        assert failures[0].severity == "error"

    def test_optional_file_missing_returns_no_failure(self, tmp_path: Path) -> None:
        contract = OutputContract(
            file="optional_file.yaml",
            required=False,
            schema_keys=[],
        )
        validator = FileContractValidator()
        failures = validator.validate_contract(contract, tmp_path)
        assert failures == []

    def test_file_exists_no_schema_keys_passes(self, tmp_path: Path) -> None:
        output_file = tmp_path / "output.yaml"
        output_file.write_text("key: value\n", encoding="utf-8")
        contract = OutputContract(file="output.yaml", required=True, schema_keys=[])
        validator = FileContractValidator()
        failures = validator.validate_contract(contract, tmp_path)
        assert failures == []

    def test_file_exists_with_required_keys_present(self, tmp_path: Path) -> None:
        output_file = tmp_path / "output.yaml"
        output_file.write_text("status: done\nresult: ok\n", encoding="utf-8")
        contract = OutputContract(
            file="output.yaml", required=True, schema_keys=["status", "result"],
        )
        validator = FileContractValidator()
        failures = validator.validate_contract(contract, tmp_path)
        assert failures == []

    def test_file_exists_with_missing_schema_key_fails(self, tmp_path: Path) -> None:
        output_file = tmp_path / "output.yaml"
        output_file.write_text("status: done\n", encoding="utf-8")
        contract = OutputContract(
            file="output.yaml", required=True, schema_keys=["status", "missing_key"],
        )
        validator = FileContractValidator()
        failures = validator.validate_contract(contract, tmp_path)
        assert any(f.rule == "required_key" for f in failures)

    def test_unparseable_yaml_returns_parseable_failure(self, tmp_path: Path) -> None:
        output_file = tmp_path / "broken.yaml"
        output_file.write_text("{ invalid yaml: [unclosed", encoding="utf-8")
        contract = OutputContract(
            file="broken.yaml", required=True, schema_keys=["key"],
        )
        validator = FileContractValidator()
        failures = validator.validate_contract(contract, tmp_path)
        assert any(f.rule == "parseable" for f in failures)


# ---------------------------------------------------------------------------
# validate_wave_contracts (lines 382-428)
# ---------------------------------------------------------------------------

class TestValidateWaveContracts:
    """Tests for validate_wave_contracts function."""

    def test_no_shards_raises_validation_error(self, tmp_path: Path) -> None:
        from trw_mcp.exceptions import ValidationError

        wave = WaveEntry(wave=1, description="Wave 1")
        with pytest.raises(ValidationError):
            validate_wave_contracts(wave, [], tmp_path)

    def test_incomplete_shard_produces_failure(self, tmp_path: Path) -> None:
        wave = WaveEntry(wave=1, description="Wave 1")
        shard = ShardCard(
            id="shard-01",
            wave=1,
            title="Test Shard",
            status=ShardStatus.PENDING,
        )
        failures = validate_wave_contracts(wave, [shard], tmp_path)
        assert any(f.rule == "shard_complete" for f in failures)

    def test_failed_shard_has_error_severity(self, tmp_path: Path) -> None:
        wave = WaveEntry(wave=1, description="Wave 1")
        shard = ShardCard(
            id="shard-01",
            wave=1,
            title="Test Shard",
            status=ShardStatus.FAILED,
        )
        failures = validate_wave_contracts(wave, [shard], tmp_path)
        failed = [f for f in failures if f.rule == "shard_complete"]
        assert len(failed) == 1
        assert failed[0].severity == "error"

    def test_pending_shard_has_warning_severity(self, tmp_path: Path) -> None:
        wave = WaveEntry(wave=1, description="Wave 1")
        shard = ShardCard(
            id="shard-01",
            wave=1,
            title="Test Shard",
            status=ShardStatus.PENDING,
        )
        failures = validate_wave_contracts(wave, [shard], tmp_path)
        pending = [f for f in failures if f.rule == "shard_complete"]
        assert len(pending) == 1
        assert pending[0].severity == "warning"

    def test_complete_shard_no_contract_passes(self, tmp_path: Path) -> None:
        wave = WaveEntry(wave=1, description="Wave 1")
        shard = ShardCard(
            id="shard-01",
            wave=1,
            title="Test Shard",
            status=ShardStatus.COMPLETE,
        )
        failures = validate_wave_contracts(wave, [shard], tmp_path)
        assert failures == []

    def test_shard_from_different_wave_is_skipped(self, tmp_path: Path) -> None:
        wave = WaveEntry(wave=1, description="Wave 1")
        shard_wave1 = ShardCard(id="shard-01", wave=1, title="Shard 1", status=ShardStatus.COMPLETE)
        shard_wave2 = ShardCard(id="shard-02", wave=2, title="Shard 2", status=ShardStatus.PENDING)
        failures = validate_wave_contracts(wave, [shard_wave1, shard_wave2], tmp_path)
        # wave2 shard should not appear in failures (it's not in wave 1)
        assert not any("shard-02" in f.field for f in failures)

    def test_complete_shard_with_integration_checklist_false_flag(self, tmp_path: Path) -> None:
        wave = WaveEntry(wave=1, description="Wave 1")
        shard = ShardCard(
            id="shard-01",
            wave=1,
            title="Test Shard",
            status=ShardStatus.COMPLETE,
            registered_in_server=False,
        )
        failures = validate_wave_contracts(wave, [shard], tmp_path)
        assert any(f.rule == "integration_checklist" for f in failures)

    def test_complete_shard_with_output_contract_file_missing(self, tmp_path: Path) -> None:
        wave = WaveEntry(wave=1, description="Wave 1")
        contract = OutputContract(file="missing.yaml", required=True, schema_keys=[])
        shard = ShardCard(
            id="shard-01",
            wave=1,
            title="Test Shard",
            status=ShardStatus.COMPLETE,
            output_contract=contract,
        )
        failures = validate_wave_contracts(wave, [shard], tmp_path)
        assert any(f.rule == "file_exists" for f in failures)


# ---------------------------------------------------------------------------
# _check_build_status (lines 566-678)
# ---------------------------------------------------------------------------

class TestCheckBuildStatus:
    """Tests for the _check_build_status function."""

    def test_returns_empty_when_build_check_disabled(self, tmp_path: Path) -> None:
        config = TRWConfig(build_check_enabled=False)
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        result = _check_build_status(trw_dir, config, "validate")
        assert result == []

    def test_returns_empty_when_enforcement_off(self, tmp_path: Path) -> None:
        config = TRWConfig(build_gate_enforcement="off")
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        result = _check_build_status(trw_dir, config, "validate")
        assert result == []

    def test_cache_missing_returns_info_advisory(self, tmp_path: Path) -> None:
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="lenient")
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "context").mkdir()
        # No build-status.yaml written
        result = _check_build_status(trw_dir, config, "validate")
        assert len(result) == 1
        assert result[0].rule == "build_cache_exists"
        assert result[0].severity == "info"

    def test_unparseable_cache_returns_warning(self, tmp_path: Path) -> None:
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)
        cache_path = context_dir / "build-status.yaml"
        cache_path.write_text("{ invalid: yaml: [unclosed", encoding="utf-8")
        result = _check_build_status(trw_dir, config, "validate")
        assert any(f.rule == "build_cache_readable" for f in result)

    def test_tests_not_passed_creates_failure(self, tmp_path: Path) -> None:
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="lenient")
        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(context_dir / "build-status.yaml", {
            "tests_passed": False,
            "mypy_clean": True,
            "coverage_pct": 90.0,
            "scope": "full",
            "timestamp": "2026-01-01T00:00:00",
        })
        result = _check_build_status(trw_dir, config, "validate")
        assert any(f.rule == "tests_passed" for f in result)

    def test_tests_not_passed_with_failures_list(self, tmp_path: Path) -> None:
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="lenient")
        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(context_dir / "build-status.yaml", {
            "tests_passed": False,
            "mypy_clean": True,
            "coverage_pct": 90.0,
            "scope": "full",
            "timestamp": "2026-01-01T00:00:00",
            "failures": ["test_foo failed", "test_bar failed"],
        })
        result = _check_build_status(trw_dir, config, "validate")
        failed = [f for f in result if f.rule == "tests_passed"]
        assert len(failed) == 1
        assert "test_foo failed" in failed[0].message

    def test_mypy_not_clean_creates_failure(self, tmp_path: Path) -> None:
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="lenient")
        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(context_dir / "build-status.yaml", {
            "tests_passed": True,
            "mypy_clean": False,
            "coverage_pct": 90.0,
            "scope": "full",
            "timestamp": "2026-01-01T00:00:00",
        })
        result = _check_build_status(trw_dir, config, "validate")
        assert any(f.rule == "mypy_clean" for f in result)

    def test_coverage_below_min_fails_at_validate(self, tmp_path: Path) -> None:
        config = TRWConfig(
            build_check_enabled=True,
            build_gate_enforcement="lenient",
            build_check_coverage_min=80.0,
        )
        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(context_dir / "build-status.yaml", {
            "tests_passed": True,
            "mypy_clean": True,
            "coverage_pct": 70.0,
            "scope": "full",
            "timestamp": "2026-01-01T00:00:00",
        })
        result = _check_build_status(trw_dir, config, "validate")
        assert any(f.rule == "coverage_min" for f in result)

    def test_coverage_not_checked_at_implement(self, tmp_path: Path) -> None:
        config = TRWConfig(
            build_check_enabled=True,
            build_gate_enforcement="lenient",
            build_check_coverage_min=80.0,
        )
        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(context_dir / "build-status.yaml", {
            "tests_passed": True,
            "mypy_clean": True,
            "coverage_pct": 50.0,  # below min
            "scope": "full",
            "timestamp": "2026-01-01T00:00:00",
        })
        result = _check_build_status(trw_dir, config, "implement")
        assert not any(f.rule == "coverage_min" for f in result)

    def test_stale_build_status_produces_warning(self, tmp_path: Path) -> None:
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)
        writer = FileStateWriter()
        # Use a timestamp from 2 hours ago (stale beyond 30 min threshold)
        stale_ts = "2020-01-01T00:00:00"
        writer.write_yaml(context_dir / "build-status.yaml", {
            "tests_passed": True,
            "mypy_clean": True,
            "coverage_pct": 90.0,
            "scope": "full",
            "timestamp": stale_ts,
        })
        result = _check_build_status(trw_dir, config, "validate")
        assert any(f.rule == "build_staleness" for f in result)

    def test_strict_enforcement_errors_at_validate(self, tmp_path: Path) -> None:
        """Strict enforcement at validate should produce errors not warnings."""
        import datetime

        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)
        writer = FileStateWriter()
        # Use a fresh timestamp (not stale) so the is_stale guard doesn't kick in
        fresh_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        writer.write_yaml(context_dir / "build-status.yaml", {
            "tests_passed": False,
            "mypy_clean": True,
            "coverage_pct": 90.0,
            "scope": "full",
            "timestamp": fresh_ts,
        })
        result = _check_build_status(trw_dir, config, "validate")
        failed = [f for f in result if f.rule == "tests_passed"]
        assert len(failed) == 1
        assert failed[0].severity == "error"

    def test_implement_phase_always_warning_even_strict(self, tmp_path: Path) -> None:
        """IMPLEMENT failures are always warning, even with strict enforcement."""
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(context_dir / "build-status.yaml", {
            "tests_passed": False,
            "mypy_clean": False,
            "coverage_pct": 50.0,
            "scope": "full",
            "timestamp": "2026-01-01T00:00:00",
        })
        result = _check_build_status(trw_dir, config, "implement")
        for failure in result:
            if failure.rule in ("tests_passed", "mypy_clean"):
                assert failure.severity == "warning", (
                    f"Expected warning at implement, got {failure.severity} for {failure.rule}"
                )

    def test_mypy_scope_only_skips_mypy_check(self, tmp_path: Path) -> None:
        """When scope is 'pytest', mypy check should not apply."""
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(context_dir / "build-status.yaml", {
            "tests_passed": True,
            "mypy_clean": False,  # mypy failed, but scope is pytest
            "coverage_pct": 90.0,
            "scope": "pytest",
            "timestamp": "2026-01-01T00:00:00",
        })
        result = _check_build_status(trw_dir, config, "validate")
        assert not any(f.rule == "mypy_clean" for f in result)


# ---------------------------------------------------------------------------
# _best_effort_build_check + _best_effort_integration_check (lines 693-736)
# ---------------------------------------------------------------------------

class TestBestEffortChecks:
    """Tests for _best_effort_build_check and _best_effort_integration_check."""

    def test_best_effort_build_check_swallows_exception(self) -> None:
        """_best_effort_build_check never raises even on total failure."""
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        failures: list[ValidationFailure] = []
        with patch(
            "trw_mcp.state.validation._check_build_status",
            side_effect=RuntimeError("unexpected"),
        ):
            _best_effort_build_check(config, "validate", failures)
        assert failures == []

    def test_best_effort_integration_check_swallows_exception(self) -> None:
        """_best_effort_integration_check never raises even on total failure."""
        failures: list[ValidationFailure] = []
        with patch(
            "trw_mcp.state.validation.check_integration",
            side_effect=RuntimeError("scan failed"),
        ):
            _best_effort_integration_check(failures)
        assert failures == []

    def test_best_effort_integration_adds_unregistered_failures(self, tmp_path: Path) -> None:
        """Unregistered tools appear as warning failures."""
        failures: list[ValidationFailure] = []
        mock_result = {
            "unregistered": ["some_tool"],
            "missing_tests": [],
        }
        src_dir = tmp_path / "trw-mcp" / "src" / "trw_mcp"
        src_dir.mkdir(parents=True)
        with (
            patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.validation.check_integration", return_value=mock_result),
        ):
            _best_effort_integration_check(failures, severity="warning")
        assert any(f.rule == "tool_registration" for f in failures)

    def test_best_effort_integration_adds_missing_test_failures(self, tmp_path: Path) -> None:
        """Missing test files appear as warning failures."""
        failures: list[ValidationFailure] = []
        mock_result = {
            "unregistered": [],
            "missing_tests": ["test_tools_foo.py"],
        }
        src_dir = tmp_path / "trw-mcp" / "src" / "trw_mcp"
        src_dir.mkdir(parents=True)
        with (
            patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.validation.check_integration", return_value=mock_result),
        ):
            _best_effort_integration_check(failures)
        assert any(f.rule == "test_coverage" for f in failures)


# ---------------------------------------------------------------------------
# check_phase_exit (lines 754-955) — all phases
# ---------------------------------------------------------------------------

class TestCheckPhaseExitResearch:
    """check_phase_exit for RESEARCH phase."""

    def test_research_exit_warns_without_synthesis(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_exit(Phase.RESEARCH, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "synthesis_exists" in rules

    def test_research_exit_passes_with_orchestrator_synthesis(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        synthesis = run_dir / "scratch" / "_orchestrator" / "research_synthesis.md"
        synthesis.write_text("# Synthesis\nFindings.", encoding="utf-8")
        config = TRWConfig()
        result = check_phase_exit(Phase.RESEARCH, run_dir, config)
        assert not any(f.rule == "synthesis_exists" for f in result.failures)

    def test_research_exit_passes_with_reports_synthesis(
        self, tmp_path: Path, writer: FileStateWriter,
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
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(phase_gate_enforcement="off")
        result = check_phase_exit(Phase.PLAN, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "plan_exists" in rules

    def test_plan_exit_passes_with_plan_md(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "reports" / "plan.md").write_text("# Plan\n", encoding="utf-8")
        config = TRWConfig(phase_gate_enforcement="off")
        result = check_phase_exit(Phase.PLAN, run_dir, config)
        assert not any(f.rule == "plan_exists" for f in result.failures)


class TestCheckPhaseExitImplement:
    """check_phase_exit for IMPLEMENT phase."""

    def test_implement_exit_warns_without_manifest(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        # shards dir exists but no manifest
        config = TRWConfig(phase_gate_enforcement="off")
        result = check_phase_exit(Phase.IMPLEMENT, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "manifest_exists" in rules

    def test_implement_exit_passes_with_manifest(
        self, tmp_path: Path, writer: FileStateWriter,
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
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_exit(Phase.VALIDATE, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "phase_test_advisory" in rules

    def test_validate_exit_advisory_is_info(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_exit(Phase.VALIDATE, run_dir, config)
        advisories = [f for f in result.failures if f.rule == "phase_test_advisory"]
        assert all(a.severity == "info" for a in advisories)


class TestCheckPhaseExitReview:
    """check_phase_exit for REVIEW phase."""

    def test_review_exit_warns_without_final_report(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_exit(Phase.REVIEW, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "final_report_exists" in rules

    def test_review_exit_warns_without_reflection_event(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(meta / "events.jsonl", {
            "event": "run_init",
            "ts": "2026-01-01T00:00:00Z",
        })
        config = TRWConfig()
        result = check_phase_exit(Phase.REVIEW, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "reflection_required" in rules

    def test_review_exit_warns_when_no_events_jsonl(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        # No events.jsonl written
        config = TRWConfig()
        result = check_phase_exit(Phase.REVIEW, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "reflection_required" in rules

    def test_review_exit_passes_with_reflection_event(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        (run_dir / "reports" / "final.md").write_text("# Final\n", encoding="utf-8")
        writer.append_jsonl(meta / "events.jsonl", {
            "event": "reflection_complete",
            "ts": "2026-01-01T12:00:00Z",
        })
        config = TRWConfig()
        result = check_phase_exit(Phase.REVIEW, run_dir, config)
        assert not any(f.rule == "reflection_required" for f in result.failures)


class TestCheckPhaseExitDeliver:
    """check_phase_exit for DELIVER phase."""

    def test_deliver_exit_warns_incomplete_run_status(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        # run.yaml status is "active", not "complete"
        config = TRWConfig()
        result = check_phase_exit(Phase.DELIVER, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "status_complete" in rules

    def test_deliver_exit_no_warning_when_run_complete(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        # Update run.yaml status to complete
        writer.write_yaml(run_dir / "meta" / "run.yaml", {
            "run_id": "20260101T000000Z-extra1234",
            "task": "extra-coverage-test",
            "status": "complete",
            "phase": "deliver",
        })
        config = TRWConfig()
        result = check_phase_exit(Phase.DELIVER, run_dir, config)
        assert not any(f.rule == "status_complete" for f in result.failures)

    def test_deliver_exit_includes_test_advisory(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_exit(Phase.DELIVER, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "phase_test_advisory" in rules

    def test_deliver_exit_warns_when_sync_missing(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        # Write events without sync event
        writer.append_jsonl(meta / "events.jsonl", {
            "event": "reflection_complete",
            "ts": "2026-01-01T00:00:00Z",
        })
        config = TRWConfig()
        result = check_phase_exit(Phase.DELIVER, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "sync_required" in rules

    def test_deliver_exit_no_sync_warning_when_synced(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(meta / "events.jsonl", {
            "event": "claude_md_sync",
            "ts": "2026-01-01T00:00:00Z",
        })
        config = TRWConfig()
        result = check_phase_exit(Phase.DELIVER, run_dir, config)
        assert not any(f.rule == "sync_required" for f in result.failures)


# ---------------------------------------------------------------------------
# check_phase_input — uncovered branches (lines 1006-1121)
# ---------------------------------------------------------------------------

class TestCheckPhaseInputUncovered:
    """Cover additional branches in check_phase_input."""

    def test_research_phase_missing_run_yaml_meta_dir(self, tmp_path: Path) -> None:
        """When meta dir itself is missing, run_yaml is absent."""
        run_dir = tmp_path / "run_no_meta"
        run_dir.mkdir()
        # No meta dir — run.yaml missing
        config = TRWConfig()
        result = check_phase_input(Phase.RESEARCH, run_dir, config)
        assert result.valid is False
        assert any(f.rule == "run_initialized" for f in result.failures)

    def test_plan_phase_warning_severity_in_lenient_mode(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        """In lenient mode missing research synthesis is a warning, not error."""
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(strict_input_criteria=False)
        result = check_phase_input(Phase.PLAN, run_dir, config)
        synthesis_failures = [f for f in result.failures if f.rule == "research_complete"]
        assert all(f.severity == "warning" for f in synthesis_failures)
        # Still valid because no errors
        assert result.valid is True

    def test_implement_warning_severity_in_lenient_mode(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        """Missing plan.md in lenient mode is warning, not error."""
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(
            strict_input_criteria=False,
            phase_gate_enforcement="off",
        )
        result = check_phase_input(Phase.IMPLEMENT, run_dir, config)
        plan_failures = [f for f in result.failures if f.rule == "plan_exists"]
        assert all(f.severity == "warning" for f in plan_failures)

    def test_validate_phase_empty_shards_dir_in_strict_mode(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        # shards dir exists but is empty
        config = TRWConfig(strict_input_criteria=True)
        result = check_phase_input(Phase.VALIDATE, run_dir, config)
        failures = [f for f in result.failures if f.rule == "implementation_complete"]
        assert len(failures) == 1
        assert failures[0].severity == "error"

    def test_review_phase_with_events_empty_file(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        """Empty events.jsonl (file exists but no entries) — no validate_passed failure."""
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        # Create events.jsonl but empty
        (meta / "events.jsonl").write_text("", encoding="utf-8")
        config = TRWConfig()
        result = check_phase_input(Phase.REVIEW, run_dir, config)
        rules = [f.rule for f in result.failures]
        # Empty events => no events to check => no validate_passed failure
        assert "validate_passed" not in rules

    def test_deliver_phase_events_with_reflection_and_complete(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        """Deliver phase passes when events contain a reflection event."""
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(meta / "events.jsonl", {
            "event": "trw_reflect_complete",
            "ts": "2026-01-01T00:00:00Z",
        })
        config = TRWConfig()
        result = check_phase_input(Phase.DELIVER, run_dir, config)
        assert not any(f.rule in ("reflection_complete", "events_exist") for f in result.failures)


# ---------------------------------------------------------------------------
# validate_prd_quality_v2 — exception branches + v1_result path (lines 1703-1761)
# ---------------------------------------------------------------------------

_MINIMAL_PRD_CONTENT = """\
---
prd:
  id: PRD-TEST-001
  title: Test PRD
  version: '1.0'
  status: draft
  priority: P1
  category: CORE
---

## 1. Problem Statement
This is a test PRD with minimal content.

## 2. Goals & Non-Goals
Goals listed here.

## 3. User Stories
User stories here.

## 4. Functional Requirements
FR01: The system shall do something.

## 5. Non-Functional Requirements
NFR01: Performance requirements.

## 6. Technical Approach
Technical approach details.

## 7. Test Strategy
Test strategy details.

## 8. Rollout Plan
Rollout plan details.

## 9. Success Metrics
Success metrics here.

## 10. Dependencies & Risks
Dependencies and risks.

## 11. Open Questions
Open questions here.

## 12. Traceability Matrix
| FR | Implementation | Test |
|----|----------------|------|
| FR01 | `src/module.py:func` | `test_tools_module.py:test_func` |
"""


class TestValidatePrdQualityV2ExceptionBranches:
    """Cover exception-handling branches in validate_prd_quality_v2."""

    def test_v1_result_precomputed_skips_v1_computation(self) -> None:
        """When v1_result is provided, it uses that instead of computing V1."""
        from trw_mcp.models.requirements import ValidationFailure

        v1_precomputed: dict[str, object] = {
            "valid": True,
            "failures": [],
            "completeness_score": 0.9,
            "traceability_coverage": 0.8,
        }
        result = validate_prd_quality_v2(_MINIMAL_PRD_CONTENT, v1_result=v1_precomputed)
        assert result.valid is True
        assert result.completeness_score == 0.9
        assert result.traceability_coverage == 0.8

    def test_v1_result_with_raw_dict_failures(self) -> None:
        """v1_result failures as raw dicts get coerced."""
        v1_precomputed: dict[str, object] = {
            "valid": False,
            "failures": [
                {"field": "f1", "rule": "r1", "message": "m1", "severity": "error"}
            ],
            "completeness_score": 0.5,
            "traceability_coverage": 0.0,
        }
        result = validate_prd_quality_v2(_MINIMAL_PRD_CONTENT, v1_result=v1_precomputed)
        assert result.valid is False
        assert len(result.failures) == 1
        assert result.failures[0].rule == "r1"

    def test_density_exception_produces_zero_score(self) -> None:
        """If score_content_density raises, dimension score defaults to 0."""
        with patch(
            "trw_mcp.state.validation.score_content_density",
            side_effect=RuntimeError("density error"),
        ):
            result = validate_prd_quality_v2(_MINIMAL_PRD_CONTENT)
        density = next(d for d in result.dimensions if d.name == "content_density")
        assert density.score == 0.0

    def test_structure_exception_produces_zero_score(self) -> None:
        """If score_structural_completeness raises, dimension score defaults to 0."""
        with patch(
            "trw_mcp.state.validation.score_structural_completeness",
            side_effect=RuntimeError("structure error"),
        ):
            result = validate_prd_quality_v2(_MINIMAL_PRD_CONTENT)
        structure = next(d for d in result.dimensions if d.name == "structural_completeness")
        assert structure.score == 0.0

    def test_traceability_exception_produces_zero_score(self) -> None:
        """If score_traceability_v2 raises, dimension score defaults to 0."""
        with patch(
            "trw_mcp.state.validation.score_traceability_v2",
            side_effect=RuntimeError("trace error"),
        ):
            result = validate_prd_quality_v2(_MINIMAL_PRD_CONTENT)
        trace = next(d for d in result.dimensions if d.name == "traceability")
        assert trace.score == 0.0

    def test_risk_level_explicit_override_in_v2(self) -> None:
        """explicit risk_level parameter applies risk scaling."""
        config = TRWConfig(risk_scaling_enabled=True)
        result = validate_prd_quality_v2(
            _MINIMAL_PRD_CONTENT,
            config=config,
            risk_level="critical",
        )
        assert result.effective_risk_level == "critical"
        assert result.risk_scaled is True

    def test_all_dimensions_zero_when_max_possible_zero(self) -> None:
        """When all max_scores are 0, total_score is 0."""
        config = TRWConfig(
            validation_density_weight=0.0,
            validation_structure_weight=0.0,
            validation_traceability_weight=0.0,
            validation_smell_weight=0.0,
            validation_readability_weight=0.0,
            validation_ears_weight=0.0,
            risk_scaling_enabled=False,
        )
        result = validate_prd_quality_v2(_MINIMAL_PRD_CONTENT, config=config)
        assert result.total_score == 0.0


# ---------------------------------------------------------------------------
# auto_progress_prds (lines 1851-1966)
# ---------------------------------------------------------------------------

class TestAutoProgressPrds:
    """Tests for auto_progress_prds function."""

    def test_returns_empty_for_unknown_phase(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        result = auto_progress_prds(
            run_dir, "unknown_phase", prds_dir, TRWConfig(),
        )
        assert result == []

    def test_returns_empty_when_no_prd_scope(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        result = auto_progress_prds(run_dir, "plan", prds_dir, TRWConfig())
        assert result == []

    def test_skips_missing_prd_file(
        self, tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        writer.write_yaml(run_dir / "meta" / "run.yaml", {
            "run_id": "20260101T000000Z-extra1234",
            "task": "test",
            "status": "active",
            "phase": "plan",
            "prd_scope": ["PRD-MISSING-001"],
        })
        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        # PRD file does not exist — should be skipped
        result = auto_progress_prds(run_dir, "plan", prds_dir, TRWConfig())
        # Result is empty because missing PRDs are logged and continued
        assert result == []

    def test_dry_run_does_not_write_file(
        self, tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        writer.write_yaml(run_dir / "meta" / "run.yaml", {
            "run_id": "20260101T000000Z-extra1234",
            "task": "test",
            "status": "active",
            "phase": "plan",
            "prd_scope": ["PRD-TEST-DRY"],
        })
        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        prd_file = _make_prd_file(prds_dir, "PRD-TEST-DRY", status="draft")
        original_content = prd_file.read_text(encoding="utf-8")

        result = auto_progress_prds(run_dir, "plan", prds_dir, TRWConfig(), dry_run=True)
        # File content should be unchanged
        assert prd_file.read_text(encoding="utf-8") == original_content
        # Result should show would_apply=True (draft → review via plan phase)
        would_apply_entries = [r for r in result if r.get("would_apply") is True]
        assert len(would_apply_entries) >= 1

    def test_applies_transition_for_approved_prd(
        self, tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Apply transition: implement phase should move an approved→implemented PRD."""
        run_dir = _make_run_dir(tmp_path, writer)
        writer.write_yaml(run_dir / "meta" / "run.yaml", {
            "run_id": "20260101T000000Z-extra1234",
            "task": "test",
            "status": "active",
            "phase": "implement",
            "prd_scope": ["PRD-TEST-IMPL"],
        })
        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        _make_prd_file(prds_dir, "PRD-TEST-IMPL", status="approved")

        result = auto_progress_prds(run_dir, "implement", prds_dir, TRWConfig())
        applied = [r for r in result if r.get("applied") is True]
        assert len(applied) == 1
        assert applied[0]["from_status"] == "approved"
        assert applied[0]["to_status"] == "implemented"

    def test_skips_terminal_status_prds(
        self, tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """PRDs already at done/merged/deprecated are skipped."""
        run_dir = _make_run_dir(tmp_path, writer)
        writer.write_yaml(run_dir / "meta" / "run.yaml", {
            "run_id": "20260101T000000Z-extra1234",
            "task": "test",
            "status": "active",
            "phase": "plan",
            "prd_scope": ["PRD-TEST-DONE"],
        })
        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        _make_prd_file(prds_dir, "PRD-TEST-DONE", status="done")

        result = auto_progress_prds(run_dir, "plan", prds_dir, TRWConfig())
        assert result == []

    def test_skips_identity_transition(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        """PRD already at target status is skipped (identity transition)."""
        run_dir = _make_run_dir(tmp_path, writer)
        writer.write_yaml(run_dir / "meta" / "run.yaml", {
            "run_id": "20260101T000000Z-extra1234",
            "task": "test",
            "status": "active",
            "phase": "plan",
            "prd_scope": ["PRD-TEST-ALREADY"],
        })
        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        # "plan" phase targets PRDStatus.REVIEW
        _make_prd_file(prds_dir, "PRD-TEST-ALREADY", status="review")

        result = auto_progress_prds(run_dir, "plan", prds_dir, TRWConfig())
        # Identity transition → skipped
        assert result == []

    def test_invalid_prd_status_in_file_is_skipped(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        """PRD file with unrecognized status is skipped gracefully."""
        run_dir = _make_run_dir(tmp_path, writer)
        writer.write_yaml(run_dir / "meta" / "run.yaml", {
            "run_id": "20260101T000000Z-extra1234",
            "task": "test",
            "status": "active",
            "phase": "plan",
            "prd_scope": ["PRD-TEST-BAD"],
        })
        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        _make_prd_file(prds_dir, "PRD-TEST-BAD", status="totally_invalid_status")

        result = auto_progress_prds(run_dir, "plan", prds_dir, TRWConfig())
        # Should not crash; bad status is logged + skipped
        assert result == []


# ---------------------------------------------------------------------------
# check_integration — edge cases (lines 1987-2043)
# ---------------------------------------------------------------------------

class TestCheckIntegrationEdgeCases:
    """Additional edge cases for check_integration."""

    def test_server_py_missing_does_not_crash(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        # No server.py — all tools go in unregistered
        (tools_dir / "mytool.py").write_text(
            "def register_mytool_tools(server):\n    pass\n",
            encoding="utf-8",
        )
        (tmp_path / "tests").mkdir(parents=True)
        result = check_integration(src_dir)
        # Without server.py, no funcs are registered
        assert "mytool" in result["unregistered"]

    def test_tool_with_call_site_but_no_import_is_registered(self, tmp_path: Path) -> None:
        """register_X_tools call in server.py (without import line) still counts."""
        src_dir = tmp_path / "src" / "pkg"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        (tmp_path / "tests").mkdir(parents=True)

        (tools_dir / "custom.py").write_text(
            "def register_custom_tools(server):\n    pass\n",
            encoding="utf-8",
        )
        # server.py has only the call site, no import line
        (src_dir / "server.py").write_text(
            "register_custom_tools(server)\n",
            encoding="utf-8",
        )
        result = check_integration(src_dir)
        assert "custom" not in result["unregistered"]
        assert result["all_registered"] is True

    def test_tool_file_read_error_is_skipped(self, tmp_path: Path) -> None:
        """Tool file that can't be read is skipped (OSError branch)."""
        src_dir = tmp_path / "src" / "pkg"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        (src_dir / "server.py").write_text("", encoding="utf-8")
        (tmp_path / "tests").mkdir(parents=True)

        # Create file with no read permissions
        tool_file = tools_dir / "unreadable.py"
        tool_file.write_text("def register_unreadable_tools(server):\n    pass\n")

        with patch("builtins.open", side_effect=OSError("permission denied")):
            # Use read_text directly to trigger OSError
            pass

        # Alternative: patch Path.read_text for just that file
        original_read_text = Path.read_text

        def patched_read_text(self: Path, **kwargs: Any) -> str:
            if self.name == "unreadable.py":
                raise OSError("permission denied")
            return original_read_text(self, **kwargs)

        with patch.object(Path, "read_text", patched_read_text):
            result = check_integration(src_dir)
        # Unreadable file skipped — not in unregistered (can't detect register func)
        assert "unreadable" not in result["unregistered"]

    def test_returns_conventions_key(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        (src_dir / "tools").mkdir(parents=True)
        (src_dir / "server.py").write_text("", encoding="utf-8")
        result = check_integration(src_dir)
        assert "conventions" in result
        assert "tool_pattern" in result["conventions"]
        assert "test_pattern" in result["conventions"]

    def test_tool_modules_scanned_count(self, tmp_path: Path) -> None:
        """tool_modules_scanned reflects only modules with register functions."""
        src_dir = tmp_path / "src" / "pkg"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        (src_dir / "server.py").write_text("", encoding="utf-8")
        (tmp_path / "tests").mkdir(parents=True)

        # 2 modules with register functions
        (tools_dir / "tool_a.py").write_text(
            "def register_tool_a_tools(server):\n    pass\n", encoding="utf-8",
        )
        (tools_dir / "tool_b.py").write_text(
            "def register_tool_b_tools(server):\n    pass\n", encoding="utf-8",
        )
        # 1 module without register function
        (tools_dir / "helper.py").write_text(
            "def some_helper():\n    pass\n", encoding="utf-8",
        )
        result = check_integration(src_dir)
        assert result["tool_modules_scanned"] == 2

    def test_init_file_is_skipped(self, tmp_path: Path) -> None:
        """__init__.py is excluded from scanning."""
        src_dir = tmp_path / "src" / "pkg"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        (src_dir / "server.py").write_text("", encoding="utf-8")
        (tools_dir / "__init__.py").write_text(
            "def register_init_tools(server):\n    pass\n", encoding="utf-8",
        )
        result = check_integration(src_dir)
        assert "__init__" not in result["unregistered"]
        assert result["tool_modules_scanned"] == 0

    def test_alternate_test_name_satisfies_missing_check(self, tmp_path: Path) -> None:
        """test_X.py (without 'tools_') also satisfies the test existence check."""
        src_dir = tmp_path / "src" / "pkg"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        (src_dir / "server.py").write_text("", encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir(parents=True)

        (tools_dir / "widget.py").write_text(
            "def register_widget_tools(server):\n    pass\n", encoding="utf-8",
        )
        # Alternate pattern: test_widget.py (not test_tools_widget.py)
        (tests_dir / "test_widget.py").write_text("# tests\n", encoding="utf-8")

        result = check_integration(src_dir)
        assert "test_tools_widget.py" not in result["missing_tests"]
