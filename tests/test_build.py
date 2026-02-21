"""Tests for trw_mcp.tools.build — build verification gate (PRD-FIX-022)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools.build import (
    _find_executable,
    _run_pytest,
    _strip_ansi,
    run_build_check,
)


# ---------------------------------------------------------------------------
# _strip_ansi
# ---------------------------------------------------------------------------


class TestStripAnsi:
    """Tests for ANSI escape code removal."""

    def test_removes_color_codes(self) -> None:
        assert _strip_ansi("\x1b[32mPASSED\x1b[0m") == "PASSED"

    def test_leaves_plain_text(self) -> None:
        assert _strip_ansi("no codes here") == "no codes here"


# ---------------------------------------------------------------------------
# _find_executable — venv resolution (PRD-FIX-022)
# ---------------------------------------------------------------------------


class TestFindExecutable:
    """Tests for _find_executable venv resolution order."""

    def test_finds_on_path(self, tmp_path: Path) -> None:
        with patch("trw_mcp.tools.build.shutil.which", return_value="/usr/bin/pytest"):
            result = _find_executable("pytest", tmp_path)
        assert result == "/usr/bin/pytest"

    def test_finds_in_dotenv(self, tmp_path: Path) -> None:
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        pytest_bin = venv_bin / "pytest"
        pytest_bin.touch()

        with patch("trw_mcp.tools.build.shutil.which", return_value=None):
            result = _find_executable("pytest", tmp_path)
        assert result == str(pytest_bin)

    def test_finds_in_venv(self, tmp_path: Path) -> None:
        venv_bin = tmp_path / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        pytest_bin = venv_bin / "pytest"
        pytest_bin.touch()

        with patch("trw_mcp.tools.build.shutil.which", return_value=None):
            result = _find_executable("pytest", tmp_path)
        assert result == str(pytest_bin)

    def test_prefers_dotenv_over_venv(self, tmp_path: Path) -> None:
        # Both .venv and venv exist — .venv should win (earlier in order)
        for name in (".venv", "venv"):
            venv_bin = tmp_path / name / "bin"
            venv_bin.mkdir(parents=True)
            (venv_bin / "pytest").touch()

        with patch("trw_mcp.tools.build.shutil.which", return_value=None):
            result = _find_executable("pytest", tmp_path)
        assert result == str(tmp_path / ".venv" / "bin" / "pytest")

    def test_falls_back_to_legacy_venv(self, tmp_path: Path) -> None:
        # Legacy: source_package_path parent's .venv
        legacy_bin = tmp_path / "trw-mcp" / ".venv" / "bin"
        legacy_bin.mkdir(parents=True)
        pytest_bin = legacy_bin / "pytest"
        pytest_bin.touch()

        with patch("trw_mcp.tools.build.shutil.which", return_value=None):
            result = _find_executable("pytest", tmp_path)
        assert result == str(pytest_bin)

    def test_returns_none_when_not_found(self, tmp_path: Path) -> None:
        with patch("trw_mcp.tools.build.shutil.which", return_value=None):
            result = _find_executable("pytest", tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# Custom test command (PRD-FIX-022)
# ---------------------------------------------------------------------------


class TestCustomTestCommand:
    """Tests for build_check_pytest_cmd custom command support."""

    def test_custom_cmd_success(self, tmp_path: Path) -> None:
        cfg = TRWConfig(build_check_pytest_cmd="echo all-tests-passed")
        with patch("trw_mcp.tools.build._config", cfg):
            result = _run_pytest(tmp_path, timeout_secs=30, extra_args="")
        assert result["tests_passed"] is True

    def test_custom_cmd_failure(self, tmp_path: Path) -> None:
        cfg = TRWConfig(build_check_pytest_cmd="false")
        with patch("trw_mcp.tools.build._config", cfg):
            result = _run_pytest(tmp_path, timeout_secs=30, extra_args="")
        assert result["tests_passed"] is False

    def test_custom_cmd_not_found(self, tmp_path: Path) -> None:
        cfg = TRWConfig(build_check_pytest_cmd="nonexistent_test_runner_xyz")
        with patch("trw_mcp.tools.build._config", cfg):
            result = _run_pytest(tmp_path, timeout_secs=30, extra_args="")
        assert result["tests_passed"] is False
        failures = result["failures"]
        assert isinstance(failures, list)
        assert len(failures) >= 1


# ---------------------------------------------------------------------------
# run_build_check integration
# ---------------------------------------------------------------------------


class TestRunBuildCheck:
    """Tests for the top-level run_build_check function."""

    def test_scope_pytest_only(self, tmp_path: Path) -> None:
        # With pytest not found, should get a failure but mypy_clean stays True
        with patch("trw_mcp.tools.build.shutil.which", return_value=None):
            status = run_build_check(tmp_path, scope="pytest")
        assert status.tests_passed is False
        assert status.mypy_clean is True  # mypy not run in pytest scope
        assert status.scope == "pytest"

    def test_scope_mypy_only(self, tmp_path: Path) -> None:
        with patch("trw_mcp.tools.build.shutil.which", return_value=None):
            status = run_build_check(tmp_path, scope="mypy")
        assert status.tests_passed is True  # pytest not run in mypy scope
        assert status.mypy_clean is False
        assert status.scope == "mypy"

    def test_has_duration(self, tmp_path: Path) -> None:
        with patch("trw_mcp.tools.build.shutil.which", return_value=None):
            status = run_build_check(tmp_path, scope="pytest")
        assert status.duration_secs >= 0
