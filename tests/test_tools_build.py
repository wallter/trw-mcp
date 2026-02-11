"""Tests for build verification gate — PRD-CORE-023.

Covers: BuildStatus model, trw_build_check tool (mocked subprocess),
cache persistence, phase gate integration, staleness detection,
and graceful degradation.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.build import BuildStatus
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.run import Phase
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.state.validation import _check_build_status
from trw_mcp.tools.build import (
    _strip_ansi,
    cache_build_status,
    run_build_check,
)


# ---------------------------------------------------------------------------
# BuildStatus model tests (FR02)
# ---------------------------------------------------------------------------


class TestBuildStatusModel:
    """Tests for the BuildStatus Pydantic model."""

    def test_defaults(self) -> None:
        status = BuildStatus()
        assert status.tests_passed is False
        assert status.mypy_clean is False
        assert status.coverage_pct == 0.0
        assert status.test_count == 0
        assert status.failure_count == 0
        assert status.failures == []
        assert status.scope == "full"
        assert status.duration_secs == 0.0

    def test_full_status(self) -> None:
        status = BuildStatus(
            tests_passed=True,
            mypy_clean=True,
            coverage_pct=92.5,
            test_count=150,
            failure_count=0,
            failures=[],
            scope="full",
            duration_secs=45.3,
        )
        assert status.tests_passed is True
        assert status.coverage_pct == 92.5
        assert status.test_count == 150

    def test_coverage_bounds(self) -> None:
        with pytest.raises(Exception):
            BuildStatus(coverage_pct=101.0)
        with pytest.raises(Exception):
            BuildStatus(coverage_pct=-1.0)

    def test_serializable(self) -> None:
        status = BuildStatus(tests_passed=True, coverage_pct=88.0)
        data = status.model_dump()
        assert isinstance(data, dict)
        assert data["tests_passed"] is True
        assert data["coverage_pct"] == 88.0


# ---------------------------------------------------------------------------
# Strip ANSI helper (FR09/RISK-009)
# ---------------------------------------------------------------------------


class TestStripAnsi:
    """Tests for ANSI escape code stripping."""

    def test_plain_text(self) -> None:
        assert _strip_ansi("hello world") == "hello world"

    def test_colored_text(self) -> None:
        assert _strip_ansi("\x1b[31mFAILED\x1b[0m test_foo") == "FAILED test_foo"

    def test_bold_text(self) -> None:
        assert _strip_ansi("\x1b[1m5 passed\x1b[0m") == "5 passed"


# ---------------------------------------------------------------------------
# Cache persistence (FR01)
# ---------------------------------------------------------------------------


class TestCacheBuildStatus:
    """Tests for build status caching to .trw/context/."""

    def test_write_and_read(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        status = BuildStatus(
            tests_passed=True,
            mypy_clean=True,
            coverage_pct=90.0,
            test_count=100,
        )
        path = cache_build_status(trw_dir, status)
        assert path.exists()
        assert path.name == "build-status.yaml"

        from trw_mcp.state.persistence import FileStateReader

        data = FileStateReader().read_yaml(path)
        assert data["tests_passed"] is True
        assert data["coverage_pct"] == 90.0

    def test_creates_context_dir(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # context/ doesn't exist yet
        status = BuildStatus()
        path = cache_build_status(trw_dir, status)
        assert path.parent.name == "context"
        assert path.exists()


# ---------------------------------------------------------------------------
# Subprocess runners (mocked) — FR03/FR04
# ---------------------------------------------------------------------------


class TestRunBuildCheck:
    """Tests for run_build_check with mocked subprocess calls."""

    @patch("trw_mcp.tools.build.shutil.which", return_value=None)
    def test_pytest_not_found(
        self, mock_which: MagicMock, tmp_path: Path,
    ) -> None:
        # No venv pytest either
        status = run_build_check(tmp_path, scope="pytest")
        assert status.tests_passed is False
        assert any("not found" in f for f in status.failures)

    @patch("trw_mcp.tools.build.shutil.which")
    @patch("trw_mcp.tools.build.subprocess.run")
    def test_pytest_passes(
        self,
        mock_run: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_which.side_effect = lambda cmd: "/usr/bin/pytest" if cmd == "pytest" else None
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="5 passed in 1.23s\nTOTAL    100    10    90%",
            stderr="",
        )
        status = run_build_check(tmp_path, scope="pytest")
        assert status.tests_passed is True
        assert status.coverage_pct == 90.0
        assert status.test_count == 5

    @patch("trw_mcp.tools.build.shutil.which")
    @patch("trw_mcp.tools.build.subprocess.run")
    def test_pytest_fails(
        self,
        mock_run: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_which.side_effect = lambda cmd: "/usr/bin/pytest" if cmd == "pytest" else None
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="3 passed, 2 failed\nFAILED tests/test_foo.py::test_bar",
            stderr="",
        )
        status = run_build_check(tmp_path, scope="pytest")
        assert status.tests_passed is False
        assert status.failure_count == 2
        assert any("FAILED" in f for f in status.failures)

    @patch("trw_mcp.tools.build.shutil.which")
    @patch("trw_mcp.tools.build.subprocess.run")
    def test_pytest_timeout(
        self,
        mock_run: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
    ) -> None:
        import subprocess

        mock_which.return_value = "/usr/bin/pytest"
        mock_run.side_effect = subprocess.TimeoutExpired("pytest", 10)
        status = run_build_check(tmp_path, scope="pytest", timeout_secs=10)
        assert status.tests_passed is False
        assert any("timed out" in f for f in status.failures)

    @patch("trw_mcp.tools.build.shutil.which")
    @patch("trw_mcp.tools.build.subprocess.run")
    def test_mypy_passes(
        self,
        mock_run: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_which.side_effect = lambda cmd: "/usr/bin/mypy" if cmd == "mypy" else None
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Success: no issues found in 42 source files",
            stderr="",
        )
        status = run_build_check(tmp_path, scope="mypy")
        assert status.mypy_clean is True
        assert status.failures == []

    @patch("trw_mcp.tools.build.shutil.which")
    @patch("trw_mcp.tools.build.subprocess.run")
    def test_mypy_errors(
        self,
        mock_run: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_which.side_effect = lambda cmd: "/usr/bin/mypy" if cmd == "mypy" else None
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="src/foo.py:10: error: Incompatible types\nFound 1 error in 1 file",
            stderr="",
        )
        status = run_build_check(tmp_path, scope="mypy")
        assert status.mypy_clean is False
        assert len(status.failures) >= 1

    @patch("trw_mcp.tools.build.shutil.which")
    @patch("trw_mcp.tools.build.subprocess.run")
    def test_full_scope(
        self,
        mock_run: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_which.return_value = "/usr/bin/tool"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="10 passed\nTOTAL    200    20    90%\nSuccess",
            stderr="",
        )
        status = run_build_check(tmp_path, scope="full")
        assert status.tests_passed is True
        assert status.mypy_clean is True
        assert status.scope == "full"
        assert status.duration_secs >= 0

    def test_duration_tracked(self, tmp_path: Path) -> None:
        with patch("trw_mcp.tools.build.shutil.which", return_value=None):
            status = run_build_check(tmp_path, scope="full")
        assert status.duration_secs >= 0


# ---------------------------------------------------------------------------
# Phase gate integration — _check_build_status (FR06/FR07/FR08)
# ---------------------------------------------------------------------------


def _write_build_cache(
    trw_dir: Path,
    *,
    tests_passed: bool = True,
    mypy_clean: bool = True,
    coverage_pct: float = 90.0,
    scope: str = "full",
    timestamp: str | None = None,
) -> Path:
    """Helper to write a build-status.yaml for phase gate tests."""
    context_dir = trw_dir / "context"
    context_dir.mkdir(parents=True, exist_ok=True)
    cache_path = context_dir / "build-status.yaml"
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    writer = FileStateWriter()
    writer.write_yaml(cache_path, {
        "tests_passed": tests_passed,
        "mypy_clean": mypy_clean,
        "coverage_pct": coverage_pct,
        "test_count": 100,
        "failure_count": 0 if tests_passed else 3,
        "failures": [] if tests_passed else ["FAILED test_a", "FAILED test_b", "FAILED test_c"],
        "timestamp": ts,
        "scope": scope,
        "duration_secs": 30.0,
    })
    return cache_path


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
        assert test_failures[0].severity == "warning"  # Always warning at implement

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
        mypy_failures = [f for f in failures if f.rule == "mypy_clean"]
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
        # Even with strict enforcement, stale results use warning
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


# ---------------------------------------------------------------------------
# Phase gate integration — check_phase_exit wiring
# ---------------------------------------------------------------------------


class TestBuildPhaseGateIntegration:
    """Integration tests: build checks wired into check_phase_exit."""

    def test_implement_gate_includes_build(self, tmp_path: Path) -> None:
        """Build check fires at IMPLEMENT phase gate."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "context").mkdir()
        _write_build_cache(trw_dir, tests_passed=False)

        # Minimal run directory
        run_dir = tmp_path / "run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (run_dir / "shards").mkdir()
        writer = FileStateWriter()
        writer.write_yaml(meta / "run.yaml", {
            "run_id": "test", "task": "test", "status": "active",
            "phase": "implement",
        })

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
        writer.write_yaml(meta / "run.yaml", {
            "run_id": "test", "task": "test", "status": "active",
            "phase": "validate",
        })

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
        writer.write_yaml(meta / "run.yaml", {
            "run_id": "test", "task": "test", "status": "active",
            "phase": "deliver",
        })

        config = TRWConfig()
        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            from trw_mcp.state.validation import check_phase_exit
            result = check_phase_exit(Phase.DELIVER, run_dir, config)

        build_failures = [f for f in result.failures if f.field.startswith("build_")]
        assert len(build_failures) >= 1


# ---------------------------------------------------------------------------
# Config defaults (FR09)
# ---------------------------------------------------------------------------


class TestBuildConfig:
    """Tests for build-related config fields."""

    def test_defaults(self) -> None:
        config = TRWConfig()
        assert config.build_check_enabled is True
        assert config.build_check_timeout_secs == 300
        assert config.build_check_coverage_min == 85.0
        assert config.build_gate_enforcement == "lenient"
        assert config.build_check_pytest_args == ""
        assert config.build_check_mypy_args == "--strict"

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_BUILD_CHECK_ENABLED", "false")
        monkeypatch.setenv("TRW_BUILD_GATE_ENFORCEMENT", "strict")
        config = TRWConfig()
        assert config.build_check_enabled is False
        assert config.build_gate_enforcement == "strict"
