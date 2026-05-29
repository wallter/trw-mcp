"""Split tests for build pytest command helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.config import TRWConfig

pytest.importorskip(
    "trw_mcp.tools.build._subprocess",
    reason="PRD-CORE-098: subprocess modules removed — these tests are obsolete, see test_build_check_reporter.py",
)
pytest.importorskip(
    "trw_mcp.tools.build._runners",
    reason="PRD-CORE-098: subprocess runner modules removed — these tests are obsolete, see test_build_check_reporter.py",
)

from trw_mcp.tools.build._runners import _run_pytest, run_build_check


class TestCustomTestCommand:
    """Tests for build_check_pytest_cmd custom command support."""

    def test_custom_cmd_success(self, tmp_path: Path) -> None:
        cfg = TRWConfig(build_check_pytest_cmd="echo all-tests-passed")
        with patch("trw_mcp.tools.build._runners.get_config", return_value=cfg):
            result = _run_pytest(tmp_path, timeout_secs=30, extra_args="")
        assert result["tests_passed"] is True

    def test_custom_cmd_failure(self, tmp_path: Path) -> None:
        cfg = TRWConfig(build_check_pytest_cmd="false")
        with patch("trw_mcp.tools.build._runners.get_config", return_value=cfg):
            result = _run_pytest(tmp_path, timeout_secs=30, extra_args="")
        assert result["tests_passed"] is False

    def test_custom_cmd_not_found(self, tmp_path: Path) -> None:
        cfg = TRWConfig(build_check_pytest_cmd="nonexistent_test_runner_xyz")
        with patch("trw_mcp.tools.build._runners.get_config", return_value=cfg):
            result = _run_pytest(tmp_path, timeout_secs=30, extra_args="")
        assert result["tests_passed"] is False
        failures = result["failures"]
        assert isinstance(failures, list)
        assert len(failures) >= 1


class TestCustomCmdErrorPath:
    """Tests for _run_pytest when build_check_pytest_cmd is set."""

    def test_custom_cmd_subprocess_error_string(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = TRWConfig(build_check_pytest_cmd="mypytest --suite all")
        monkeypatch.setattr("trw_mcp.tools.build._runners.get_config", lambda: config)

        with patch(
            "trw_mcp.tools.build._runners._run_subprocess",
            return_value="mypytest executable not found",
        ):
            result = _run_pytest(tmp_path, 30, "")

        assert result["tests_passed"] is False
        assert result["test_count"] == 0
        failures = result["failures"]
        assert isinstance(failures, list)
        assert any("not found" in str(f) for f in failures)

    def test_custom_cmd_success(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = TRWConfig(build_check_pytest_cmd="mypytest --fast")
        monkeypatch.setattr("trw_mcp.tools.build._runners.get_config", lambda: config)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "All tests passed"
        mock_result.stderr = ""

        with patch("trw_mcp.tools.build._runners._run_subprocess", return_value=mock_result):
            result = _run_pytest(tmp_path, 30, "")

        assert result["tests_passed"] is True
        assert result["failure_count"] == 0

    def test_custom_cmd_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = TRWConfig(build_check_pytest_cmd="mypytest --suite all")
        monkeypatch.setattr("trw_mcp.tools.build._runners.get_config", lambda: config)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = "FAILED tests/test_foo.py::test_bar\nsome other output"
        mock_result.stderr = ""

        with patch("trw_mcp.tools.build._runners._run_subprocess", return_value=mock_result):
            result = _run_pytest(tmp_path, 30, "")

        assert result["tests_passed"] is False
        assert result["failure_count"] == 1


class TestPytestExtraArgs:
    """Line 209: extra_args passed to pytest command."""

    @patch("trw_mcp.tools.build._subprocess.subprocess.run")
    @patch("trw_mcp.tools.build._subprocess.shutil.which", return_value="/usr/bin/pytest")
    def test_extra_args_appended_to_cmd(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="1 passed", stderr="")
        run_build_check(tmp_path, scope="pytest", pytest_args="-k test_foo --no-header")
        cmd = mock_run.call_args.args[0]
        assert "-k" in cmd
        assert "test_foo" in cmd
        assert "--no-header" in cmd
