"""Split tests for obsolete build runner paths."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip(
    "trw_mcp.tools.build._subprocess",
    reason="PRD-CORE-098: subprocess modules removed — these tests are obsolete, see test_build_check_reporter.py",
)
pytest.importorskip(
    "trw_mcp.tools.build._runners",
    reason="PRD-CORE-098: subprocess runner modules removed — these tests are obsolete, see test_build_check_reporter.py",
)

from trw_mcp.tools.build._runners import _collect_failures, run_build_check


@pytest.mark.skip(
    reason="run_build_check removed in PRD-CORE-098 — replaced by result reporter API, see test_build_check_reporter.py"
)
class TestRunBuildCheck:
    """Tests for the top-level run_build_check function (OBSOLETE — PRD-CORE-098)."""

    def test_scope_pytest_only(self, tmp_path: Path) -> None:
        with patch("trw_mcp.tools.build._subprocess.shutil.which", return_value=None):
            status = run_build_check(tmp_path, scope="pytest")
        assert status.tests_passed is False
        assert status.mypy_clean is True
        assert status.scope == "pytest"

    def test_scope_mypy_only(self, tmp_path: Path) -> None:
        with patch("trw_mcp.tools.build._subprocess.shutil.which", return_value=None):
            status = run_build_check(tmp_path, scope="mypy")
        assert status.tests_passed is True
        assert status.mypy_clean is False
        assert status.scope == "mypy"

    def test_has_duration(self, tmp_path: Path) -> None:
        with patch("trw_mcp.tools.build._subprocess.shutil.which", return_value=None):
            status = run_build_check(tmp_path, scope="pytest")
        assert status.duration_secs >= 0


class TestRunMypyErrorPath:
    """Tests for _run_mypy OSError/timeout path (line 282)."""

    @patch("trw_mcp.tools.build._subprocess.shutil.which", return_value="/usr/bin/mypy")
    def test_mypy_subprocess_error_string(self, mock_which: MagicMock, tmp_path: Path) -> None:
        with patch(
            "trw_mcp.tools.build._runners._run_subprocess",
            return_value="mypy timed out after 30s",
        ):
            status = run_build_check(tmp_path, scope="mypy")

        assert status.mypy_clean is False
        assert any("timed out" in f for f in status.failures)

    @patch("trw_mcp.tools.build._subprocess.shutil.which", return_value="/usr/bin/mypy")
    def test_mypy_oserror_returns_error_string(self, mock_which: MagicMock, tmp_path: Path) -> None:
        with patch(
            "trw_mcp.tools.build._runners._run_subprocess",
            return_value="mypy executable not found",
        ):
            status = run_build_check(tmp_path, scope="mypy")

        assert status.mypy_clean is False
        assert len(status.failures) == 1


class TestCollectFailures:
    """Tests for _collect_failures edge cases."""

    def test_raw_not_a_list(self) -> None:
        result = _collect_failures({"failures": "not a list"})
        assert result == []

    def test_raw_is_none(self) -> None:
        result = _collect_failures({"failures": None})
        assert result == []

    def test_raw_is_missing_key(self) -> None:
        result = _collect_failures({})
        assert result == []

    def test_raw_is_list_of_strings(self) -> None:
        result = _collect_failures({"failures": ["FAILED a", "FAILED b"]})
        assert result == ["FAILED a", "FAILED b"]

    def test_raw_is_list_converts_to_str(self) -> None:
        result = _collect_failures({"failures": [42, True]})
        assert result == ["42", "True"]
