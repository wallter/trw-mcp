"""Obsolete subprocess-runner tests for build verification gate."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests._tools_build_support import _write_build_cache  # noqa: F401


@pytest.mark.skip(
    reason="run_build_check removed in PRD-CORE-098 — replaced by result reporter API, see test_build_check_reporter.py"
)
class TestRunBuildCheck:
    """Tests for run_build_check with mocked subprocess calls (OBSOLETE — PRD-CORE-098)."""

    @patch("trw_mcp.tools.build._subprocess.shutil.which", return_value=None)
    def test_pytest_not_found(
        self,
        mock_which: MagicMock,
        tmp_path: Path,
    ) -> None:
        status = run_build_check(tmp_path, scope="pytest")
        assert status.tests_passed is False
        assert any("not found" in f for f in status.failures)

    @patch("trw_mcp.tools.build._subprocess.shutil.which")
    @patch("trw_mcp.tools.build._subprocess.subprocess.run")
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

    @patch("trw_mcp.tools.build._subprocess.shutil.which")
    @patch("trw_mcp.tools.build._subprocess.subprocess.run")
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

    @patch("trw_mcp.tools.build._subprocess.shutil.which")
    @patch("trw_mcp.tools.build._subprocess.subprocess.run")
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

    @patch("trw_mcp.tools.build._subprocess.shutil.which")
    @patch("trw_mcp.tools.build._subprocess.subprocess.run")
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

    @patch("trw_mcp.tools.build._subprocess.shutil.which")
    @patch("trw_mcp.tools.build._subprocess.subprocess.run")
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

    @patch("trw_mcp.tools.build._subprocess.shutil.which")
    @patch("trw_mcp.tools.build._subprocess.subprocess.run")
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
        with patch("trw_mcp.tools.build._subprocess.shutil.which", return_value=None):
            status = run_build_check(tmp_path, scope="full")
        assert status.duration_secs >= 0
