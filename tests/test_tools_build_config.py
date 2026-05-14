"""Tests for build verification config and obsolete config wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests._tools_build_support import _write_build_cache  # noqa: F401
from tests.conftest import get_tools_sync
from trw_mcp.models.build import BuildStatus
from trw_mcp.models.config import TRWConfig


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


@pytest.mark.skip(reason="Calls run_build_check removed in PRD-CORE-098 — see test_build_check_reporter.py")
class TestBuildConfigWiring:
    """Tests for config-driven paths in build tools (OBSOLETE — PRD-CORE-098)."""

    @patch("trw_mcp.tools.build._subprocess.subprocess.run")
    @patch("trw_mcp.tools.build._subprocess.shutil.which", return_value="/usr/bin/pytest")
    def test_pytest_default_cwd_is_build_root(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR01: Default config → cwd = project_root (where tests/ lives)."""
        monkeypatch.setattr("trw_mcp.tools.build._runners.get_config", lambda: TRWConfig())
        mock_run.return_value = MagicMock(returncode=0, stdout="1 passed", stderr="")
        run_build_check(tmp_path, scope="pytest")
        cwd = mock_run.call_args.kwargs["cwd"]
        assert cwd == str(tmp_path)

    @patch("trw_mcp.tools.build._subprocess.subprocess.run")
    @patch("trw_mcp.tools.build._subprocess.shutil.which", return_value="/usr/bin/pytest")
    def test_pytest_custom_source_path_cwd(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR01: source_package_path='src' → cwd = project_root (build_root='.')."""
        config = TRWConfig(
            source_package_path="src",
            tests_relative_path="tests",
            source_package_name="myapp",
        )
        monkeypatch.setattr("trw_mcp.tools.build._runners.get_config", lambda: config)
        mock_run.return_value = MagicMock(returncode=0, stdout="1 passed", stderr="")
        run_build_check(tmp_path, scope="pytest")
        cwd = mock_run.call_args.kwargs["cwd"]
        assert cwd == str(tmp_path / ".")

    @patch("trw_mcp.tools.build._subprocess.subprocess.run")
    @patch("trw_mcp.tools.build._subprocess.shutil.which", return_value="/usr/bin/pytest")
    def test_pytest_cov_target_from_config(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR01: source_package_name='myapp' → --cov=myapp."""
        config = TRWConfig(source_package_name="myapp")
        monkeypatch.setattr("trw_mcp.tools.build._runners.get_config", lambda: config)
        mock_run.return_value = MagicMock(returncode=0, stdout="1 passed", stderr="")
        run_build_check(tmp_path, scope="pytest")
        cmd = mock_run.call_args.args[0]
        assert "--cov=myapp" in cmd

    @patch("trw_mcp.tools.build._subprocess.subprocess.run")
    @patch("trw_mcp.tools.build._subprocess.shutil.which", return_value="/usr/bin/pytest")
    def test_pytest_test_dir_strips_build_root(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR01: tests_relative_path default → 'tests/' used directly (cwd = project_root)."""
        monkeypatch.setattr("trw_mcp.tools.build._runners.get_config", lambda: TRWConfig())
        mock_run.return_value = MagicMock(returncode=0, stdout="1 passed", stderr="")
        run_build_check(tmp_path, scope="pytest")
        cmd = mock_run.call_args.args[0]
        assert any("tests" in c for c in cmd)

    @patch("trw_mcp.tools.build._subprocess.subprocess.run")
    @patch("trw_mcp.tools.build._subprocess.shutil.which", return_value="/usr/bin/mypy")
    def test_mypy_default_cwd_is_build_root(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR02: Default config → mypy cwd = project_root / 'trw-mcp'."""
        monkeypatch.setattr("trw_mcp.tools.build._runners.get_config", lambda: TRWConfig())
        mock_run.return_value = MagicMock(returncode=0, stdout="Success", stderr="")
        run_build_check(tmp_path, scope="mypy")
        cwd = mock_run.call_args.kwargs["cwd"]
        assert cwd == str(tmp_path / "trw-mcp")

    @patch("trw_mcp.tools.build._subprocess.subprocess.run")
    @patch("trw_mcp.tools.build._subprocess.shutil.which", return_value="/usr/bin/mypy")
    def test_mypy_custom_source_target(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR02: Custom config → src_target='src/myapp/'."""
        config = TRWConfig(
            source_package_path="src",
            source_package_name="myapp",
        )
        monkeypatch.setattr("trw_mcp.tools.build._runners.get_config", lambda: config)
        mock_run.return_value = MagicMock(returncode=0, stdout="Success", stderr="")
        run_build_check(tmp_path, scope="mypy")
        cmd = mock_run.call_args.args[0]
        assert cmd[-1] == "src/myapp/"

    @patch("trw_mcp.tools.build._subprocess.subprocess.run")
    @patch("trw_mcp.tools.build._subprocess.shutil.which", return_value="/usr/bin/mypy")
    def test_mypy_default_source_target(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR02: Default config → src_target='src/trw_mcp/'."""
        monkeypatch.setattr("trw_mcp.tools.build._runners.get_config", lambda: TRWConfig())
        mock_run.return_value = MagicMock(returncode=0, stdout="Success", stderr="")
        run_build_check(tmp_path, scope="mypy")
        cmd = mock_run.call_args.args[0]
        assert cmd[-1] == "src/trw_mcp/"

    def test_find_executable_custom_venv_path(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR03: source_package_path='src' → checks project_root/.venv/bin/."""
        config = TRWConfig(source_package_path="src")
        monkeypatch.setattr("trw_mcp.tools.build._subprocess.get_config", lambda: config)
        monkeypatch.setattr("trw_mcp.tools.build._subprocess.shutil.which", lambda _: None)

        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "pytest").touch()

        result = _find_executable("pytest", tmp_path)
        assert result == str(venv_bin / "pytest")

    def test_find_executable_default_venv_path(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR03: Default config → checks project_root/trw-mcp/.venv/bin/."""
        monkeypatch.setattr("trw_mcp.tools.build._subprocess.get_config", lambda: TRWConfig())
        monkeypatch.setattr("trw_mcp.tools.build._subprocess.shutil.which", lambda _: None)

        venv_bin = tmp_path / "trw-mcp" / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "pytest").touch()

        result = _find_executable("pytest", tmp_path)
        assert result == str(venv_bin / "pytest")

    @patch("trw_mcp.tools.build._subprocess.subprocess.run")
    @patch("trw_mcp.tools.build._subprocess.shutil.which", return_value="/usr/bin/pytest")
    def test_pytest_empty_config_falls_back(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """NFR02/RISK-001: Empty string config fields fall back to TRW defaults."""
        config = TRWConfig(
            source_package_path="",
            tests_relative_path="",
            source_package_name="",
        )
        monkeypatch.setattr("trw_mcp.tools.build._runners.get_config", lambda: config)
        mock_run.return_value = MagicMock(returncode=0, stdout="1 passed", stderr="")
        status = run_build_check(tmp_path, scope="pytest")
        assert status.tests_passed is True
        cmd = mock_run.call_args.args[0]
        cwd = mock_run.call_args.kwargs["cwd"]
        assert "--cov=trw_mcp" in cmd
        assert cwd == str(tmp_path)

    @patch("trw_mcp.tools.build._subprocess.subprocess.run")
    @patch("trw_mcp.tools.build._subprocess.shutil.which", return_value="/usr/bin/mypy")
    def test_mypy_empty_config_falls_back(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """NFR02/RISK-001: Empty string mypy config falls back to TRW defaults."""
        config = TRWConfig(
            source_package_path="",
            source_package_name="",
        )
        monkeypatch.setattr("trw_mcp.tools.build._runners.get_config", lambda: config)
        mock_run.return_value = MagicMock(returncode=0, stdout="Success", stderr="")
        status = run_build_check(tmp_path, scope="mypy")
        assert status.mypy_clean is True
        cmd = mock_run.call_args.args[0]
        cwd = mock_run.call_args.kwargs["cwd"]
        assert cmd[-1] == "src/trw_mcp/"
        assert cwd == str(tmp_path / "trw-mcp")


@pytest.mark.skip(reason="Patches run_build_check removed in PRD-CORE-098 — see test_build_check_reporter.py")
class TestMinCoverageThreshold:
    """Tests for the min_coverage parameter (OBSOLETE — PRD-CORE-098)."""

    @patch("trw_mcp.tools.build._registration.get_config")
    @patch("trw_mcp.tools.build._registration.resolve_project_root")
    @patch("trw_mcp.tools.build._registration.resolve_trw_dir")
    @patch("trw_mcp.tools.build._registration.run_build_check")
    def test_coverage_below_threshold_fails(
        self,
        mock_run: MagicMock,
        mock_trw_dir: MagicMock,
        mock_proj_root: MagicMock,
        mock_get_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """min_coverage=80 with 75% actual → tests_passed=False."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "context").mkdir()
        mock_trw_dir.return_value = trw_dir
        mock_proj_root.return_value = tmp_path
        mock_get_config.return_value = TRWConfig(
            build_check_enabled=True,
            build_check_timeout_secs=300,
            build_check_pytest_args="",
            build_check_mypy_args="--strict",
        )

        mock_run.return_value = BuildStatus(
            tests_passed=True,
            mypy_clean=True,
            coverage_pct=75.0,
            test_count=100,
        )

        from fastmcp import FastMCP

        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)

        tools = get_tools_sync(server)
        assert "trw_build_check" in tools
        tool_fn = tools["trw_build_check"].fn

        result = tool_fn(scope="pytest", min_coverage=80.0)
        assert result["tests_passed"] is False
        assert result["coverage_threshold_failed"] is True
        assert result["coverage_threshold"] == 80.0
        assert "75.0%" in str(result["coverage_threshold_message"])

    @patch("trw_mcp.tools.build._registration.get_config")
    @patch("trw_mcp.tools.build._registration.resolve_project_root")
    @patch("trw_mcp.tools.build._registration.resolve_trw_dir")
    @patch("trw_mcp.tools.build._registration.run_build_check")
    def test_coverage_meets_threshold_passes(
        self,
        mock_run: MagicMock,
        mock_trw_dir: MagicMock,
        mock_proj_root: MagicMock,
        mock_get_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """min_coverage=80 with 90% actual → tests_passed stays True."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "context").mkdir()
        mock_trw_dir.return_value = trw_dir
        mock_proj_root.return_value = tmp_path
        mock_get_config.return_value = TRWConfig(
            build_check_enabled=True,
            build_check_timeout_secs=300,
            build_check_pytest_args="",
            build_check_mypy_args="--strict",
        )

        mock_run.return_value = BuildStatus(
            tests_passed=True,
            mypy_clean=True,
            coverage_pct=90.0,
            test_count=100,
        )

        from fastmcp import FastMCP

        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)

        tools = get_tools_sync(server)
        assert "trw_build_check" in tools
        tool_fn = tools["trw_build_check"].fn

        result = tool_fn(scope="pytest", min_coverage=80.0)
        assert result["tests_passed"] is True
        assert "coverage_threshold_failed" not in result

    @patch("trw_mcp.tools.build._registration.get_config")
    @patch("trw_mcp.tools.build._registration.resolve_project_root")
    @patch("trw_mcp.tools.build._registration.resolve_trw_dir")
    @patch("trw_mcp.tools.build._registration.run_build_check")
    def test_no_min_coverage_skips_check(
        self,
        mock_run: MagicMock,
        mock_trw_dir: MagicMock,
        mock_proj_root: MagicMock,
        mock_get_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """min_coverage=None → no threshold enforcement."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "context").mkdir()
        mock_trw_dir.return_value = trw_dir
        mock_proj_root.return_value = tmp_path
        mock_get_config.return_value = TRWConfig(
            build_check_enabled=True,
            build_check_timeout_secs=300,
            build_check_pytest_args="",
            build_check_mypy_args="--strict",
        )

        mock_run.return_value = BuildStatus(
            tests_passed=True,
            mypy_clean=True,
            coverage_pct=50.0,
            test_count=100,
        )

        from fastmcp import FastMCP

        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)

        tools = get_tools_sync(server)
        assert "trw_build_check" in tools
        tool_fn = tools["trw_build_check"].fn

        result = tool_fn(scope="pytest")
        assert result["tests_passed"] is True
        assert "coverage_threshold_failed" not in result
