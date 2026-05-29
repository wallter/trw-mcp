"""Extra coverage tests for trw_mcp/state/validation.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import ValidationFailure
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.state.validation import (
    _best_effort_build_check,
    _best_effort_integration_check,
    _check_build_status,
)


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
        writer.write_yaml(
            context_dir / "build-status.yaml",
            {
                "tests_passed": False,
                "mypy_clean": True,
                "coverage_pct": 90.0,
                "scope": "full",
                "timestamp": "2026-01-01T00:00:00",
            },
        )
        result = _check_build_status(trw_dir, config, "validate")
        assert any(f.rule == "tests_passed" for f in result)

    def test_tests_not_passed_with_failures_list(self, tmp_path: Path) -> None:
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="lenient")
        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(
            context_dir / "build-status.yaml",
            {
                "tests_passed": False,
                "mypy_clean": True,
                "coverage_pct": 90.0,
                "scope": "full",
                "timestamp": "2026-01-01T00:00:00",
                "failures": ["test_foo failed", "test_bar failed"],
            },
        )
        result = _check_build_status(trw_dir, config, "validate")
        failed = [f for f in result if f.rule == "tests_passed"]
        assert len(failed) == 1
        assert "test_foo failed" in failed[0].message

    def test_static_checks_not_clean_creates_failure(self, tmp_path: Path) -> None:
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="lenient")
        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(
            context_dir / "build-status.yaml",
            {
                "tests_passed": True,
                "static_checks_clean": False,
                "mypy_clean": True,
                "coverage_pct": 90.0,
                "scope": "full",
                "timestamp": "2026-01-01T00:00:00",
            },
        )
        result = _check_build_status(trw_dir, config, "validate")
        assert any(f.rule == "static_checks_clean" for f in result)

    def test_legacy_mypy_not_clean_still_creates_failure(self, tmp_path: Path) -> None:
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="lenient")
        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(
            context_dir / "build-status.yaml",
            {
                "tests_passed": True,
                "mypy_clean": False,
                "coverage_pct": 90.0,
                "scope": "full",
                "timestamp": "2026-01-01T00:00:00",
            },
        )
        result = _check_build_status(trw_dir, config, "validate")
        assert any(f.rule == "static_checks_clean" for f in result)

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
        writer.write_yaml(
            context_dir / "build-status.yaml",
            {
                "tests_passed": True,
                "mypy_clean": True,
                "coverage_pct": 70.0,
                "scope": "full",
                "timestamp": "2026-01-01T00:00:00",
            },
        )
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
        writer.write_yaml(
            context_dir / "build-status.yaml",
            {
                "tests_passed": True,
                "mypy_clean": True,
                "coverage_pct": 50.0,
                "scope": "full",
                "timestamp": "2026-01-01T00:00:00",
            },
        )
        result = _check_build_status(trw_dir, config, "implement")
        assert not any(f.rule == "coverage_min" for f in result)

    def test_stale_build_status_produces_warning(self, tmp_path: Path) -> None:
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(
            context_dir / "build-status.yaml",
            {
                "tests_passed": True,
                "mypy_clean": True,
                "coverage_pct": 90.0,
                "scope": "full",
                "timestamp": "2020-01-01T00:00:00",
            },
        )
        result = _check_build_status(trw_dir, config, "validate")
        assert any(f.rule == "build_staleness" for f in result)

    def test_strict_enforcement_errors_at_validate(self, tmp_path: Path) -> None:
        import datetime

        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)
        writer = FileStateWriter()
        fresh_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        writer.write_yaml(
            context_dir / "build-status.yaml",
            {
                "tests_passed": False,
                "mypy_clean": True,
                "coverage_pct": 90.0,
                "scope": "full",
                "timestamp": fresh_ts,
            },
        )
        result = _check_build_status(trw_dir, config, "validate")
        failed = [f for f in result if f.rule == "tests_passed"]
        assert len(failed) == 1
        assert failed[0].severity == "error"

    def test_implement_phase_always_warning_even_strict(self, tmp_path: Path) -> None:
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(
            context_dir / "build-status.yaml",
            {
                "tests_passed": False,
                "mypy_clean": False,
                "coverage_pct": 50.0,
                "scope": "full",
                "timestamp": "2026-01-01T00:00:00",
            },
        )
        result = _check_build_status(trw_dir, config, "implement")
        for failure in result:
            if failure.rule in ("tests_passed", "static_checks_clean"):
                assert failure.severity == "warning", (
                    f"Expected warning at implement, got {failure.severity} for {failure.rule}"
                )

    def test_test_only_scope_skips_static_check(self, tmp_path: Path) -> None:
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(
            context_dir / "build-status.yaml",
            {
                "tests_passed": True,
                "mypy_clean": False,
                "coverage_pct": 90.0,
                "scope": "pytest",
                "timestamp": "2026-01-01T00:00:00",
            },
        )
        result = _check_build_status(trw_dir, config, "validate")
        assert not any(f.rule == "static_checks_clean" for f in result)


class TestBestEffortChecks:
    """Tests for _best_effort_build_check and _best_effort_integration_check."""

    def test_best_effort_build_check_swallows_exception(self) -> None:
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        failures: list[ValidationFailure] = []
        with patch(
            "trw_mcp.state.validation.phase_gates_build._check_build_status",
            side_effect=RuntimeError("unexpected"),
        ):
            _best_effort_build_check(config, "validate", failures)
        assert failures == []

    def test_best_effort_integration_check_swallows_exception(self) -> None:
        failures: list[ValidationFailure] = []
        with patch(
            "trw_mcp.state.validation.integration_check.check_integration",
            side_effect=RuntimeError("scan failed"),
        ):
            _best_effort_integration_check(failures)
        assert failures == []

    def test_best_effort_integration_adds_unregistered_failures(self, tmp_path: Path) -> None:
        failures: list[ValidationFailure] = []
        mock_result = {
            "unregistered": ["some_tool"],
            "missing_tests": [],
        }
        src_dir = tmp_path / "trw-mcp" / "src" / "trw_mcp"
        src_dir.mkdir(parents=True)
        with (
            patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.validation.integration_check.check_integration", return_value=mock_result),
        ):
            _best_effort_integration_check(failures, severity="warning")
        assert any(f.rule == "tool_registration" for f in failures)

    def test_best_effort_integration_adds_missing_test_failures(self, tmp_path: Path) -> None:
        failures: list[ValidationFailure] = []
        mock_result = {
            "unregistered": [],
            "missing_tests": ["test_tools_foo.py"],
        }
        src_dir = tmp_path / "trw-mcp" / "src" / "trw_mcp"
        src_dir.mkdir(parents=True)
        with (
            patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.validation.integration_check.check_integration", return_value=mock_result),
        ):
            _best_effort_integration_check(failures)
        assert any(f.rule == "test_coverage" for f in failures)
