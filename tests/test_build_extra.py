"""Extra coverage tests for tools/build.py — targeting uncovered branches.

Covers:
- Lines 112-113: _run_subprocess OSError path
- Line 209: custom_cmd subprocess returns error string
- Line 282: _run_mypy returns error string on OSError
- Line 299: _collect_failures when raw is not a list
- Lines 377-442: trw_build_check tool function (disabled, run_path, event logging)
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools.build import (
    _collect_failures,
    _run_pytest,
    _run_subprocess,
    run_build_check,
)

# ---------------------------------------------------------------------------
# Lines 112-113: _run_subprocess OSError path
# ---------------------------------------------------------------------------


class TestRunSubprocess:
    """Tests for _run_subprocess internal helper."""

    def test_oserror_returns_error_string(self, tmp_path: Path) -> None:
        """Lines 112-113: subprocess.run raises OSError → returns error string."""
        with patch(
            "trw_mcp.tools.build.subprocess.run",
            side_effect=OSError("no such file"),
        ):
            result = _run_subprocess(["fakecmd", "--arg"], tmp_path, 30)

        assert isinstance(result, str)
        assert "fakecmd" in result
        assert "not found" in result

    def test_timeout_returns_error_string(self, tmp_path: Path) -> None:
        """Lines 110-111: subprocess.run raises TimeoutExpired → returns error string."""
        with patch(
            "trw_mcp.tools.build.subprocess.run",
            side_effect=subprocess.TimeoutExpired("fakecmd", 5),
        ):
            result = _run_subprocess(["fakecmd"], tmp_path, 5)

        assert isinstance(result, str)
        assert "timed out" in result

    def test_success_returns_completed_process(self, tmp_path: Path) -> None:
        """Happy path: subprocess.run returns CompletedProcess."""
        mock_proc = MagicMock(spec=subprocess.CompletedProcess)
        mock_proc.returncode = 0
        mock_proc.stdout = "output"
        mock_proc.stderr = ""

        with patch("trw_mcp.tools.build.subprocess.run", return_value=mock_proc):
            result = _run_subprocess(["echo", "hello"], tmp_path, 10)

        assert result is mock_proc


# ---------------------------------------------------------------------------
# Line 209: custom_cmd subprocess error path
# ---------------------------------------------------------------------------


class TestCustomCmdErrorPath:
    """Tests for _run_pytest when build_check_pytest_cmd is set."""

    def test_custom_cmd_subprocess_error_string(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Line 209: custom_cmd path returns error string (OSError/timeout)."""
        config = TRWConfig(build_check_pytest_cmd="mypytest --suite all")
        monkeypatch.setattr("trw_mcp.tools.build._config", config)

        with patch(
            "trw_mcp.tools.build._run_subprocess",
            return_value="mypytest executable not found",
        ):
            result = _run_pytest(tmp_path, 30, "")

        assert result["tests_passed"] is False
        assert result["test_count"] == 0
        failures = result["failures"]
        assert isinstance(failures, list)
        assert any("not found" in str(f) for f in failures)

    def test_custom_cmd_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """custom_cmd happy path — returncode 0."""
        config = TRWConfig(build_check_pytest_cmd="mypytest --fast")
        monkeypatch.setattr("trw_mcp.tools.build._config", config)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "All tests passed"
        mock_result.stderr = ""

        with patch("trw_mcp.tools.build._run_subprocess", return_value=mock_result):
            result = _run_pytest(tmp_path, 30, "")

        assert result["tests_passed"] is True
        assert result["failure_count"] == 0

    def test_custom_cmd_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """custom_cmd failure — returncode != 0 extracts FAILED lines."""
        config = TRWConfig(build_check_pytest_cmd="mypytest --suite all")
        monkeypatch.setattr("trw_mcp.tools.build._config", config)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = "FAILED tests/test_foo.py::test_bar\nsome other output"
        mock_result.stderr = ""

        with patch("trw_mcp.tools.build._run_subprocess", return_value=mock_result):
            result = _run_pytest(tmp_path, 30, "")

        assert result["tests_passed"] is False
        assert result["failure_count"] == 1


class TestPytestExtraArgs:
    """Line 209: extra_args passed to pytest command."""

    @patch("trw_mcp.tools.build.subprocess.run")
    @patch("trw_mcp.tools.build.shutil.which", return_value="/usr/bin/pytest")
    def test_extra_args_appended_to_cmd(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Line 209: non-empty extra_args are split and appended to pytest cmd."""
        mock_run.return_value = MagicMock(returncode=0, stdout="1 passed", stderr="")
        run_build_check(tmp_path, scope="pytest", pytest_args="-k test_foo --no-header")
        cmd = mock_run.call_args.args[0]
        assert "-k" in cmd
        assert "test_foo" in cmd
        assert "--no-header" in cmd


# ---------------------------------------------------------------------------
# Line 282: _run_mypy subprocess error string
# ---------------------------------------------------------------------------


class TestRunMypyErrorPath:
    """Tests for _run_mypy OSError/timeout path (line 282)."""

    @patch("trw_mcp.tools.build.shutil.which", return_value="/usr/bin/mypy")
    def test_mypy_subprocess_error_string(
        self, mock_which: MagicMock, tmp_path: Path
    ) -> None:
        """Line 282: _run_subprocess returns a string → mypy_clean=False."""
        with patch(
            "trw_mcp.tools.build._run_subprocess",
            return_value="mypy timed out after 30s",
        ):
            status = run_build_check(tmp_path, scope="mypy")

        assert status.mypy_clean is False
        assert any("timed out" in f for f in status.failures)

    @patch("trw_mcp.tools.build.shutil.which", return_value="/usr/bin/mypy")
    def test_mypy_oserror_returns_error_string(
        self, mock_which: MagicMock, tmp_path: Path
    ) -> None:
        """mypy OSError: subprocess returns error string."""
        with patch(
            "trw_mcp.tools.build._run_subprocess",
            return_value="mypy executable not found",
        ):
            status = run_build_check(tmp_path, scope="mypy")

        assert status.mypy_clean is False
        assert len(status.failures) == 1


# ---------------------------------------------------------------------------
# Line 299: _collect_failures when raw is not a list
# ---------------------------------------------------------------------------


class TestCollectFailures:
    """Tests for _collect_failures edge cases."""

    def test_raw_not_a_list(self) -> None:
        """Line 299: raw is not a list → returns empty list."""
        result = _collect_failures({"failures": "not a list"})
        assert result == []

    def test_raw_is_none(self) -> None:
        """raw is None → returns empty list (key missing)."""
        result = _collect_failures({"failures": None})
        assert result == []

    def test_raw_is_missing_key(self) -> None:
        """No 'failures' key → returns empty list."""
        result = _collect_failures({})
        assert result == []

    def test_raw_is_list_of_strings(self) -> None:
        """Happy path: raw is list of strings."""
        result = _collect_failures({"failures": ["FAILED a", "FAILED b"]})
        assert result == ["FAILED a", "FAILED b"]

    def test_raw_is_list_converts_to_str(self) -> None:
        """Non-string list items are cast to str."""
        result = _collect_failures({"failures": [42, True]})
        assert result == ["42", "True"]


# ---------------------------------------------------------------------------
# Lines 377-442: trw_build_check tool function
# ---------------------------------------------------------------------------


class TestTrwBuildCheckTool:
    """Tests for the trw_build_check MCP tool closure (lines 377-442)."""

    def _get_tool(self) -> object:
        """Import and register tools to get the closure."""
        from fastmcp import FastMCP

        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)
        # Access registered tool by looking at server tools
        return server

    def test_build_check_disabled_returns_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Line 396-400: build_check_enabled=False returns skipped status."""
        config = TRWConfig(build_check_enabled=False)
        monkeypatch.setattr("trw_mcp.tools.build._config", config)

        from fastmcp import FastMCP

        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)

        # Call the tool directly by importing the registered function
        # We patch resolve_trw_dir and resolve_project_root so we don't need real paths
        with (
            patch("trw_mcp.tools.build.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.build.resolve_project_root", return_value=tmp_path),
        ):
            # Get the tool function from registered tools dict
            tools_dict = server._tool_manager._tools
            tool = tools_dict["trw_build_check"]
            result = tool.fn(scope="full", run_path=None, timeout_secs=None)

        assert result["status"] == "skipped"
        assert "build_check_enabled" in result["reason"]

    def test_build_check_runs_and_caches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Lines 402-440: build check runs, caches result, returns dict."""
        (tmp_path / ".trw").mkdir()

        config = TRWConfig(build_check_enabled=True)
        monkeypatch.setattr("trw_mcp.tools.build._config", config)

        from fastmcp import FastMCP

        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)

        with (
            patch("trw_mcp.tools.build.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.build.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.tools.build.run_build_check") as mock_rbc,
            patch("trw_mcp.tools.build.cache_build_status") as mock_cache,
        ):
            from trw_mcp.models.build import BuildStatus

            mock_status = BuildStatus(
                tests_passed=True,
                mypy_clean=True,
                coverage_pct=92.0,
                test_count=50,
                failure_count=0,
                scope="full",
                duration_secs=10.0,
            )
            mock_rbc.return_value = mock_status
            mock_cache.return_value = tmp_path / ".trw" / "context" / "build-status.yaml"

            tools_dict = server._tool_manager._tools
            tool = tools_dict["trw_build_check"]
            result = tool.fn(scope="full", run_path=None, timeout_secs=None)

        assert result["tests_passed"] is True
        assert result["mypy_clean"] is True
        assert result["coverage_pct"] == 92.0
        assert result["test_count"] == 50
        assert "cache_path" in result

    def test_build_check_with_run_path_and_events(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Lines 419-431: run_path provided → logs build_check_complete event."""
        (tmp_path / ".trw").mkdir()

        # Create the meta/events.jsonl dir so event logging fires
        run_dir = tmp_path / "runs" / "test-run"
        meta_dir = run_dir / "meta"
        meta_dir.mkdir(parents=True)

        config = TRWConfig(build_check_enabled=True)
        monkeypatch.setattr("trw_mcp.tools.build._config", config)

        from fastmcp import FastMCP

        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)

        with (
            patch("trw_mcp.tools.build.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.build.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.tools.build.run_build_check") as mock_rbc,
            patch("trw_mcp.tools.build.cache_build_status") as mock_cache,
        ):
            from trw_mcp.models.build import BuildStatus

            mock_status = BuildStatus(
                tests_passed=True,
                mypy_clean=True,
                coverage_pct=88.0,
                test_count=30,
                failure_count=0,
                scope="pytest",
                duration_secs=5.0,
            )
            mock_rbc.return_value = mock_status
            mock_cache.return_value = tmp_path / ".trw" / "context" / "build-status.yaml"

            tools_dict = server._tool_manager._tools
            tool = tools_dict["trw_build_check"]
            result = tool.fn(
                scope="pytest",
                run_path=str(run_dir),
                timeout_secs=60,
            )

        assert result["tests_passed"] is True
        assert result["scope"] == "pytest"
        # Verify event was logged
        events_path = meta_dir / "events.jsonl"
        assert events_path.exists()
        import json

        with events_path.open() as fh:
            events = [json.loads(line) for line in fh if line.strip()]
        assert any(e.get("event") == "build_check_complete" for e in events)

    def test_build_check_run_path_no_meta_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Lines 422-423: run_path given but meta dir missing → no event logged."""
        (tmp_path / ".trw").mkdir()
        run_dir = tmp_path / "runs" / "nonexistent-run"
        # Do NOT create meta directory

        config = TRWConfig(build_check_enabled=True)
        monkeypatch.setattr("trw_mcp.tools.build._config", config)

        from fastmcp import FastMCP

        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)

        with (
            patch("trw_mcp.tools.build.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.build.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.tools.build.run_build_check") as mock_rbc,
            patch("trw_mcp.tools.build.cache_build_status") as mock_cache,
        ):
            from trw_mcp.models.build import BuildStatus

            mock_status = BuildStatus(
                tests_passed=False,
                mypy_clean=True,
                coverage_pct=70.0,
                test_count=10,
                failure_count=2,
                scope="full",
                duration_secs=3.0,
            )
            mock_rbc.return_value = mock_status
            mock_cache.return_value = tmp_path / ".trw" / "context" / "build-status.yaml"

            tools_dict = server._tool_manager._tools
            tool = tools_dict["trw_build_check"]
            result = tool.fn(
                scope="full",
                run_path=str(run_dir),
                timeout_secs=None,
            )

        # Tool still returns results even without event logging
        assert result["tests_passed"] is False
        assert result["failure_count"] == 2

    def test_build_check_timeout_capped_at_600(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Line 404-407: timeout_secs capped at 600."""
        (tmp_path / ".trw").mkdir()
        config = TRWConfig(build_check_enabled=True)
        monkeypatch.setattr("trw_mcp.tools.build._config", config)

        from fastmcp import FastMCP

        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)

        captured_timeout: list[int] = []

        with (
            patch("trw_mcp.tools.build.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.build.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.tools.build.cache_build_status") as mock_cache,
            patch("trw_mcp.tools.build.run_build_check") as mock_rbc,
        ):
            from trw_mcp.models.build import BuildStatus

            def capture_rbc(proj_root: Path, **kwargs: object) -> BuildStatus:
                captured_timeout.append(int(str(kwargs.get("timeout_secs", 0))))
                return BuildStatus(
                    tests_passed=True,
                    mypy_clean=True,
                    coverage_pct=80.0,
                    test_count=5,
                    scope="full",
                    duration_secs=1.0,
                )

            mock_rbc.side_effect = capture_rbc
            mock_cache.return_value = tmp_path / ".trw" / "context" / "build-status.yaml"

            tools_dict = server._tool_manager._tools
            tool = tools_dict["trw_build_check"]
            tool.fn(scope="full", run_path=None, timeout_secs=9999)

        assert captured_timeout[0] == 600

    def test_build_check_uses_config_default_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Line 405: timeout_secs=None uses config default."""
        (tmp_path / ".trw").mkdir()
        config = TRWConfig(build_check_enabled=True, build_check_timeout_secs=120)
        monkeypatch.setattr("trw_mcp.tools.build._config", config)

        from fastmcp import FastMCP

        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)

        captured_timeout: list[int] = []

        with (
            patch("trw_mcp.tools.build.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.build.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.tools.build.cache_build_status") as mock_cache,
            patch("trw_mcp.tools.build.run_build_check") as mock_rbc,
        ):
            from trw_mcp.models.build import BuildStatus

            def capture_rbc(proj_root: Path, **kwargs: object) -> BuildStatus:
                captured_timeout.append(int(str(kwargs.get("timeout_secs", 0))))
                return BuildStatus(
                    tests_passed=True,
                    mypy_clean=True,
                    coverage_pct=80.0,
                    test_count=5,
                    scope="full",
                    duration_secs=1.0,
                )

            mock_rbc.side_effect = capture_rbc
            mock_cache.return_value = tmp_path / ".trw" / "context" / "build-status.yaml"

            tools_dict = server._tool_manager._tools
            tool = tools_dict["trw_build_check"]
            tool.fn(scope="full", run_path=None, timeout_secs=None)

        assert captured_timeout[0] == 120
