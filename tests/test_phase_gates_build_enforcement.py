from __future__ import annotations

from pathlib import Path

from tests._phase_gates_build_support import _make_trw_dir, _write_build_status
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.validation.phase_gates_build import _check_build_status


class TestCheckBuildStatusPassingBuild:
    """Tests for a fresh, passing build-status cache."""

    def test_all_pass_returns_no_failures(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, tests_passed=True, mypy_clean=True, coverage_pct=90.0)
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "validate")
        assert result == []

    def test_tests_failed_adds_failure(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, tests_passed=False)
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "implement")
        rules = [f.rule for f in result]
        assert "tests_passed" in rules

    def test_static_checks_failed_adds_failure(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, mypy_clean=False)
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "validate")
        rules = [f.rule for f in result]
        assert "static_checks_clean" in rules

    def test_coverage_low_at_validate_adds_failure(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, coverage_pct=50.0)
        config = TRWConfig(
            build_check_enabled=True,
            build_gate_enforcement="strict",
            build_check_coverage_min=80.0,
        )
        result = _check_build_status(trw_dir, config, "validate")
        rules = [f.rule for f in result]
        assert "coverage_min" in rules

    def test_coverage_low_at_implement_no_failure(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, coverage_pct=10.0)
        config = TRWConfig(
            build_check_enabled=True,
            build_gate_enforcement="strict",
            build_check_coverage_min=80.0,
        )
        result = _check_build_status(trw_dir, config, "implement")
        rules = [f.rule for f in result]
        assert "coverage_min" not in rules

    def test_coverage_low_at_deliver_adds_failure(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, coverage_pct=40.0)
        config = TRWConfig(
            build_check_enabled=True,
            build_gate_enforcement="strict",
            build_check_coverage_min=80.0,
        )
        result = _check_build_status(trw_dir, config, "deliver")
        rules = [f.rule for f in result]
        assert "coverage_min" in rules

    def test_implement_uses_warning_severity(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, tests_passed=False)
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "implement")
        tests_failure = [f for f in result if f.rule == "tests_passed"]
        assert tests_failure
        assert tests_failure[0].severity == "warning"

    def test_strict_validate_uses_error_severity(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, tests_passed=False)
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "validate")
        tests_failure = [f for f in result if f.rule == "tests_passed"]
        assert tests_failure
        assert tests_failure[0].severity == "error"

    def test_static_scope_checks_static_status(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, mypy_clean=False, scope="static")
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "validate")
        rules = [f.rule for f in result]
        assert "static_checks_clean" in rules

    def test_scope_pytest_no_static_check(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, mypy_clean=False, scope="pytest")
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "validate")
        rules = [f.rule for f in result]
        assert "static_checks_clean" not in rules
