from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import PRDStatus
from trw_mcp.models.run import Phase
from trw_mcp.state.validation import _check_prd_enforcement


class TestValidationRunTypeReadFailure:
    """Lines 469-470: StateError reading run.yaml for run_type check."""

    def test_check_prd_enforcement_run_yaml_read_error_continues(self, tmp_path: Path) -> None:
        run_path = tmp_path / "run"
        meta = run_path / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_text("run_id: test\n")
        config = TRWConfig(trw_dir=str(tmp_path / ".trw"))

        with (
            patch("trw_mcp.state.prd_utils.discover_governing_prds", return_value=[]),
            patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.persistence.FileStateReader.read_yaml", side_effect=StateError("corrupt yaml")),
        ):
            failures = _check_prd_enforcement(run_path, config, PRDStatus.APPROVED, "implement")

        assert len(failures) == 1
        assert failures[0].rule == "prd_discovery"


class TestValidationPrdReadFailed:
    """Lines 529-531: PRD file read raises OSError during status check."""

    def test_prd_read_failure_adds_readable_failure(self, tmp_path: Path) -> None:
        run_path = tmp_path / "run"
        (run_path / "meta").mkdir(parents=True)
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)
        (prds_dir / "PRD-TEST-001.md").write_text("---\nprd:\n  status: draft\n---\n")
        config = TRWConfig(trw_dir=str(tmp_path / ".trw"))

        with (
            patch("trw_mcp.state.prd_utils.discover_governing_prds", return_value=["PRD-TEST-001"]),
            patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.prd_utils.parse_frontmatter", side_effect=OSError("cannot read")),
        ):
            failures = _check_prd_enforcement(run_path, config, PRDStatus.APPROVED, "implement")

        readable_failures = [f for f in failures if f.rule == "prd_readable"]
        assert len(readable_failures) == 1
        assert "PRD-TEST-001" in readable_failures[0].message


class TestValidationBuildStatusStaleness:
    """Lines 622-623: ValueError/TypeError when parsing build timestamp."""

    def test_build_status_unparseable_timestamp_continues(self, tmp_path: Path) -> None:
        from trw_mcp.state.validation import _check_build_status

        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)
        (context_dir / "build-status.yaml").write_text("tests_passed: true\nmypy_clean: true\ntimestamp: not-a-date\n")
        config = TRWConfig(trw_dir=str(trw_dir))
        object.__setattr__(config, "build_check_enabled", True)
        object.__setattr__(config, "build_gate_enforcement", "strict")

        failures = _check_build_status(trw_dir, config, "validate")
        staleness_failures = [f for f in failures if f.rule == "build_staleness"]
        assert len(staleness_failures) == 0


class TestValidationIntegrationScannerException:
    """Lines 735-736: exception inside _best_effort_integration_check is swallowed."""

    def test_integration_check_exception_never_blocks(self, tmp_path: Path) -> None:
        from trw_mcp.state.validation import _best_effort_integration_check

        failures: list = []
        with patch("trw_mcp.state._paths.resolve_project_root", side_effect=RuntimeError("no root")):
            _best_effort_integration_check(failures)
        assert failures == []


class TestValidationImplementPhaseInvalidStatus:
    """Lines 815-816: invalid prd_required_status_for_implement falls back to APPROVED."""

    def test_implement_phase_invalid_required_status_fallback(self, tmp_path: Path) -> None:
        from trw_mcp.state.validation import check_phase_exit

        run_path = tmp_path / "run"
        (run_path / "meta").mkdir(parents=True)
        (run_path / "reports").mkdir()
        (run_path / "scratch" / "_orchestrator").mkdir(parents=True)
        (run_path / "shards").mkdir()
        (run_path / "meta" / "run.yaml").write_text("run_id: test\ntask: test\nstatus: active\nphase: implement\n")
        config = TRWConfig(trw_dir=str(tmp_path / ".trw"))
        object.__setattr__(config, "prd_required_status_for_implement", "INVALID_STATUS")

        with (
            patch(
                "trw_mcp.state.validation._phase_gates_exits._check_prd_enforcement", return_value=[]
            ) as prd_enforcement,
            patch("trw_mcp.state.validation._phase_gates_exits._best_effort_build_check") as build_check,
        ):
            result = check_phase_exit(Phase.IMPLEMENT, run_path, config)

        prd_enforcement.assert_called_once()
        build_check.assert_called_once()
        assert result is not None
        assert hasattr(result, "valid")
        assert isinstance(result.failures, list)


class TestValidationBuildStatusTimestampParseError:
    """Line 622-623: explicit ValueError and TypeError in timestamp parsing."""

    @pytest.mark.parametrize("bad_ts", ["not-a-date", "2026-13-45T99:99:99", ""])
    def test_bad_timestamp_treated_as_fresh(self, tmp_path: Path, bad_ts: str) -> None:
        from trw_mcp.state.validation import _check_build_status

        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)
        cache = trw_dir / "context" / "build-status.yaml"

        if bad_ts:
            cache.write_text(f"tests_passed: true\nmypy_clean: true\ntimestamp: '{bad_ts}'\n")
        else:
            cache.write_text("tests_passed: true\nmypy_clean: true\n")

        config = TRWConfig(trw_dir=str(trw_dir))
        object.__setattr__(config, "build_check_enabled", True)
        object.__setattr__(config, "build_gate_enforcement", "strict")

        failures = _check_build_status(trw_dir, config, "validate")
        staleness = [f for f in failures if f.rule == "build_staleness"]
        assert len(staleness) == 0
