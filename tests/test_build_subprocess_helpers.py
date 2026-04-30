"""Split tests for build subprocess helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.config import TRWConfig

pytest.importorskip(
    "trw_mcp.tools.build._subprocess",
    reason="PRD-CORE-098: subprocess modules removed — these tests are obsolete, see test_build_check_reporter.py",
)

from trw_mcp.tools.build._subprocess import _find_executable, _run_subprocess, _strip_ansi


class TestStripAnsi:
    """Tests for ANSI escape code removal."""

    def test_removes_color_codes(self) -> None:
        assert _strip_ansi("\x1b[32mPASSED\x1b[0m") == "PASSED"

    def test_leaves_plain_text(self) -> None:
        assert _strip_ansi("no codes here") == "no codes here"


class TestFindExecutable:
    """Tests for _find_executable venv resolution order."""

    def test_finds_on_path(self, tmp_path: Path) -> None:
        with patch("trw_mcp.tools.build._subprocess.shutil.which", return_value="/usr/bin/pytest"):
            result = _find_executable("pytest", tmp_path)
        assert result == "/usr/bin/pytest"

    def test_finds_in_dotenv(self, tmp_path: Path) -> None:
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        pytest_bin = venv_bin / "pytest"
        pytest_bin.touch()

        with patch("trw_mcp.tools.build._subprocess.shutil.which", return_value=None):
            result = _find_executable("pytest", tmp_path)
        assert result == str(pytest_bin)

    def test_finds_in_venv(self, tmp_path: Path) -> None:
        venv_bin = tmp_path / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        pytest_bin = venv_bin / "pytest"
        pytest_bin.touch()

        with patch("trw_mcp.tools.build._subprocess.shutil.which", return_value=None):
            result = _find_executable("pytest", tmp_path)
        assert result == str(pytest_bin)

    def test_prefers_dotenv_over_venv(self, tmp_path: Path) -> None:
        for name in (".venv", "venv"):
            venv_bin = tmp_path / name / "bin"
            venv_bin.mkdir(parents=True)
            (venv_bin / "pytest").touch()

        with patch("trw_mcp.tools.build._subprocess.shutil.which", return_value=None):
            result = _find_executable("pytest", tmp_path)
        assert result == str(tmp_path / ".venv" / "bin" / "pytest")

    def test_falls_back_to_legacy_venv(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("trw_mcp.tools.build._subprocess.get_config", lambda: TRWConfig())
        legacy_bin = tmp_path / "trw-mcp" / ".venv" / "bin"
        legacy_bin.mkdir(parents=True)
        pytest_bin = legacy_bin / "pytest"
        pytest_bin.touch()

        with patch("trw_mcp.tools.build._subprocess.shutil.which", return_value=None):
            result = _find_executable("pytest", tmp_path)
        assert result == str(pytest_bin)

    def test_returns_none_when_not_found(self, tmp_path: Path) -> None:
        with patch("trw_mcp.tools.build._subprocess.shutil.which", return_value=None):
            result = _find_executable("pytest", tmp_path)
        assert result is None


class TestRunSubprocess:
    """Tests for _run_subprocess internal helper."""

    def test_oserror_returns_error_string(self, tmp_path: Path) -> None:
        with patch(
            "trw_mcp.tools.build._subprocess.subprocess.run",
            side_effect=OSError("no such file"),
        ):
            result = _run_subprocess(["fakecmd", "--arg"], tmp_path, 30)

        assert isinstance(result, str)
        assert "fakecmd" in result
        assert "not found" in result

    def test_timeout_returns_error_string(self, tmp_path: Path) -> None:
        with patch(
            "trw_mcp.tools.build._subprocess.subprocess.run",
            side_effect=subprocess.TimeoutExpired("fakecmd", 5),
        ):
            result = _run_subprocess(["fakecmd"], tmp_path, 5)

        assert isinstance(result, str)
        assert "timed out" in result

    def test_success_returns_completed_process(self, tmp_path: Path) -> None:
        mock_proc = MagicMock(spec=subprocess.CompletedProcess)
        mock_proc.returncode = 0
        mock_proc.stdout = "output"
        mock_proc.stderr = ""

        with patch("trw_mcp.tools.build._subprocess.subprocess.run", return_value=mock_proc):
            result = _run_subprocess(["echo", "hello"], tmp_path, 10)

        assert result is mock_proc
