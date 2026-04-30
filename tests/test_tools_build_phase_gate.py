"""Tests for build verification phase gate behavior and wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests._tools_build_support import _write_build_cache
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.run import Phase
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.state.validation import _check_build_status


class TestBuildPhaseGate:
    """Tests for _check_build_status phase gate helper."""

    def test_no_cache_returns_info(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = TRWConfig()
        failures = _check_build_status(trw_dir, config, "validate")
        assert len(failures) == 1
        assert failures[0].severity == "info"
        assert "No build status cached" in failures[0].message

    def test_disabled_returns_empty(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = TRWConfig(build_check_enabled=False)
        failures = _check_build_status(trw_dir, config, "validate")
        assert failures == []

    def test_enforcement_off_returns_empty(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = TRWConfig(build_gate_enforcement="off")
        failures = _check_build_status(trw_dir, config, "validate")
        assert failures == []

    def test_passing_build_no_failures(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        _write_build_cache(trw_dir)
        config = TRWConfig()
        failures = _check_build_status(trw_dir, config, "validate")
        assert failures == []

    def test_failed_tests_at_implement_warning(self, tmp_path: Path) -> None:
        """FR06: IMPLEMENT gate always uses warning severity."""
        trw_dir = tmp_path / ".trw"
        _write_build_cache(trw_dir, tests_passed=False)
        config = TRWConfig(build_gate_enforcement="strict")
        failures = _check_build_status(trw_dir, config, "implement")
        test_failures = [f for f in failures if f.rule == "tests_passed"]
        assert len(test_failures) == 1
        assert test_failures[0].severity == "warning"

    def test_failed_tests_at_validate_strict(self, tmp_path: Path) -> None:
        """FR07: VALIDATE with strict enforcement = error severity."""
        trw_dir = tmp_path / ".trw"
        _write_build_cache(trw_dir, tests_passed=False)
        config = TRWConfig(build_gate_enforcement="strict")
        failures = _check_build_status(trw_dir, config, "validate")
        test_failures = [f for f in failures if f.rule == "tests_passed"]
        assert len(test_failures) == 1
        assert test_failures[0].severity == "error"

    def test_failed_tests_at_validate_lenient(self, tmp_path: Path) -> None:
        """FR07: VALIDATE with lenient enforcement = warning severity."""
        trw_dir = tmp_path / ".trw"
        _write_build_cache(trw_dir, tests_passed=False)
        config = TRWConfig(build_gate_enforcement="lenient")
        failures = _check_build_status(trw_dir, config, "validate")
        test_failures = [f for f in failures if f.rule == "tests_passed"]
        assert len(test_failures) == 1
        assert test_failures[0].severity == "warning"

    def test_failed_tests_at_deliver_strict(self, tmp_path: Path) -> None:
        """FR08: DELIVER with strict enforcement = error severity."""
        trw_dir = tmp_path / ".trw"
        _write_build_cache(trw_dir, tests_passed=False)
        config = TRWConfig(build_gate_enforcement="strict")
        failures = _check_build_status(trw_dir, config, "deliver")
        test_failures = [f for f in failures if f.rule == "tests_passed"]
        assert len(test_failures) == 1
        assert test_failures[0].severity == "error"

    def test_mypy_errors_detected(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        _write_build_cache(trw_dir, mypy_clean=False)
        config = TRWConfig()
        failures = _check_build_status(trw_dir, config, "validate")
        mypy_failures = [f for f in failures if f.rule == "type_check_clean"]
        assert len(mypy_failures) == 1

    def test_coverage_below_min(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        _write_build_cache(trw_dir, coverage_pct=50.0)
        config = TRWConfig(build_check_coverage_min=85.0)
        failures = _check_build_status(trw_dir, config, "validate")
        cov_failures = [f for f in failures if f.rule == "coverage_min"]
        assert len(cov_failures) == 1
        assert "50.0%" in cov_failures[0].message

    def test_coverage_not_checked_at_implement(self, tmp_path: Path) -> None:
        """Coverage is only checked at validate/deliver."""
        trw_dir = tmp_path / ".trw"
        _write_build_cache(trw_dir, coverage_pct=50.0)
        config = TRWConfig(build_check_coverage_min=85.0)
        failures = _check_build_status(trw_dir, config, "implement")
        cov_failures = [f for f in failures if f.rule == "coverage_min"]
        assert cov_failures == []

    def test_stale_build_downgraded(self, tmp_path: Path) -> None:
        """FR10: Stale results (>30 min) downgraded to warning."""
        trw_dir = tmp_path / ".trw"
        old_ts = "2020-01-01T00:00:00+00:00"
        _write_build_cache(trw_dir, tests_passed=False, timestamp=old_ts)
        config = TRWConfig(build_gate_enforcement="strict")
        failures = _check_build_status(trw_dir, config, "validate")
        test_failures = [f for f in failures if f.rule == "tests_passed"]
        assert all(f.severity == "warning" for f in test_failures)
        staleness_failures = [f for f in failures if f.rule == "build_staleness"]
        assert len(staleness_failures) == 1

    def test_failure_snippet_in_message(self, tmp_path: Path) -> None:
        """Failure messages include a snippet of the first failure."""
        trw_dir = tmp_path / ".trw"
        _write_build_cache(trw_dir, tests_passed=False)
        config = TRWConfig()
        failures = _check_build_status(trw_dir, config, "validate")
        test_failures = [f for f in failures if f.rule == "tests_passed"]
        assert len(test_failures) == 1
        assert "FAILED test_a" in test_failures[0].message
        assert "+2 more" in test_failures[0].message

    def test_mypy_only_scope_skips_mypy_check_for_pytest(self, tmp_path: Path) -> None:
        """When scope='pytest', mypy_clean is not checked."""
        trw_dir = tmp_path / ".trw"
        _write_build_cache(trw_dir, mypy_clean=False, scope="pytest")
        config = TRWConfig()
        failures = _check_build_status(trw_dir, config, "validate")
        mypy_failures = [f for f in failures if f.rule == "mypy_clean"]
        assert mypy_failures == []


class TestBuildPhaseGateIntegration:
    """Integration tests: build checks wired into check_phase_exit."""

    def test_implement_gate_includes_build(self, tmp_path: Path) -> None:
        """Build check fires at IMPLEMENT phase gate."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "context").mkdir()
        _write_build_cache(trw_dir, tests_passed=False)

        run_dir = tmp_path / "run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (run_dir / "shards").mkdir()
        writer = FileStateWriter()
        writer.write_yaml(
            meta / "run.yaml",
            {
                "run_id": "test",
                "task": "test",
                "status": "active",
                "phase": "implement",
            },
        )

        config = TRWConfig()
        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            from trw_mcp.state.validation import check_phase_exit

            result = check_phase_exit(Phase.IMPLEMENT, run_dir, config)

        build_failures = [f for f in result.failures if f.field.startswith("build_")]
        assert len(build_failures) >= 1

    def test_validate_gate_includes_build(self, tmp_path: Path) -> None:
        """Build check fires at VALIDATE phase gate."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "context").mkdir()
        _write_build_cache(trw_dir, tests_passed=False)

        run_dir = tmp_path / "run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (run_dir / "validation").mkdir()
        writer = FileStateWriter()
        writer.write_yaml(
            meta / "run.yaml",
            {
                "run_id": "test",
                "task": "test",
                "status": "active",
                "phase": "validate",
            },
        )

        config = TRWConfig()
        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            from trw_mcp.state.validation import check_phase_exit

            result = check_phase_exit(Phase.VALIDATE, run_dir, config)

        build_failures = [f for f in result.failures if f.field.startswith("build_")]
        assert len(build_failures) >= 1

    def test_deliver_gate_includes_build(self, tmp_path: Path) -> None:
        """Build check fires at DELIVER phase gate."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "context").mkdir()
        _write_build_cache(trw_dir, tests_passed=False)

        run_dir = tmp_path / "run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (run_dir / "reports").mkdir()
        writer = FileStateWriter()
        writer.write_yaml(
            meta / "run.yaml",
            {
                "run_id": "test",
                "task": "test",
                "status": "active",
                "phase": "deliver",
            },
        )

        config = TRWConfig()
        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            from trw_mcp.state.validation import check_phase_exit

            result = check_phase_exit(Phase.DELIVER, run_dir, config)

        build_failures = [f for f in result.failures if f.field.startswith("build_")]
        assert len(build_failures) >= 1
