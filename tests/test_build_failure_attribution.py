"""Tests for build-check failure attribution (PRD-IMPROVE-MCP-02 FR1).

Covers the heuristic that tags each reported test failure as
``likely_introduced`` / ``likely_pre_existing`` / ``unknown`` by comparing
the failing test's file against the current working-tree change set, plus
the integration path through ``trw_build_check``.

The module shells out to git; tests patch ``changed_files`` (or the
subprocess) so they are deterministic and have no I/O dependency on the
real repo state.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import get_tools_sync, make_test_server
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools.build import _failure_attribution as fa

_MOD = "trw_mcp.tools.build._failure_attribution"


class TestAttributeFailures:
    def test_untouched_file_is_pre_existing(self) -> None:
        """A failure in a file NOT in the diff -> likely_pre_existing."""
        with patch(f"{_MOD}.changed_files", return_value={"foo.py", "src/foo.py"}):
            result = fa.attribute_failures(
                ["tests/test_bar.py::TestBar::test_thing FAILED"]
            )
        assert result is not None
        assert result["likely_pre_existing"] == 1
        assert result["likely_introduced"] == 0
        item = result["per_failure"][0]
        assert item["classification"] == "likely_pre_existing"
        assert item["test_file"] == "tests/test_bar.py"
        assert "no current change" in item["reason"]

    def test_modified_test_file_is_introduced(self) -> None:
        """A failure whose own test file is in the diff -> likely_introduced."""
        with patch(
            f"{_MOD}.changed_files",
            return_value={"tests/test_bar.py", "test_bar.py"},
        ):
            result = fa.attribute_failures(
                ["tests/test_bar.py::TestBar::test_thing FAILED"]
            )
        assert result is not None
        assert result["likely_introduced"] == 1
        assert result["likely_pre_existing"] == 0
        assert result["per_failure"][0]["classification"] == "likely_introduced"

    def test_related_source_file_is_introduced(self) -> None:
        """A failure whose TARGET source (by name stem) changed -> introduced."""
        # test_widget.py exercises widget.py; widget.py is in the diff.
        with patch(
            f"{_MOD}.changed_files",
            return_value={"src/trw_mcp/widget.py", "widget.py"},
        ):
            result = fa.attribute_failures(
                ["tests/test_widget.py::test_renders FAILED"]
            )
        assert result is not None
        assert result["likely_introduced"] == 1
        assert result["per_failure"][0]["classification"] == "likely_introduced"

    def test_git_unavailable_degrades_to_unknown(self) -> None:
        """changed_files() returning None -> every failure tagged unknown, no raise."""
        with patch(f"{_MOD}.changed_files", return_value=None):
            result = fa.attribute_failures(
                ["tests/test_bar.py::test_thing FAILED"]
            )
        assert result is not None
        assert result["unknown"] == 1
        assert result["likely_introduced"] == 0
        assert result["likely_pre_existing"] == 0
        assert result["per_failure"][0]["classification"] == "unknown"
        assert "git unavailable" in result["per_failure"][0]["reason"]

    def test_unparseable_failure_is_unknown(self) -> None:
        """A failure string with no file path -> unknown classification."""
        with patch(f"{_MOD}.changed_files", return_value=set()):
            result = fa.attribute_failures(["something broke, no path here"])
        assert result is not None
        assert result["unknown"] == 1
        assert result["per_failure"][0]["test_file"] is None

    def test_no_failures_returns_none(self) -> None:
        """Empty failure list -> None (nothing to attribute)."""
        assert fa.attribute_failures([]) is None

    def test_mixed_set_counts_and_summary(self) -> None:
        """Mixed failures produce correct counts + a human-readable summary."""
        with patch(
            f"{_MOD}.changed_files",
            return_value={"tests/test_a.py", "test_a.py"},
        ):
            result = fa.attribute_failures(
                [
                    "tests/test_a.py::test_one FAILED",  # introduced
                    "tests/test_b.py::test_two FAILED",  # pre-existing
                    "opaque failure no path",  # unknown
                ]
            )
        assert result is not None
        assert result["likely_introduced"] == 1
        assert result["likely_pre_existing"] == 1
        assert result["unknown"] == 1
        assert "1 likely yours" in result["summary"]
        assert "1 pre-existing on this tree" in result["summary"]
        assert "not proof" in result["summary"]

    def test_directory_only_change_entry_is_skipped(self) -> None:
        """A diff entry that is a bare directory (no '.') is not stem-matched."""
        # 'tests/sub' is a directory-only basename in the change set; it must
        # not spuriously match test_sub.py via the related-source path.
        with patch(f"{_MOD}.changed_files", return_value={"pkg/sub", "sub"}):
            result = fa.attribute_failures(["tests/test_widget.py::t FAILED"])
        assert result is not None
        assert result["likely_pre_existing"] == 1

    def test_changed_files_count_only_counts_files(self) -> None:
        """changed_files_count reflects file paths (with a dot), not dirs."""
        with patch(
            f"{_MOD}.changed_files",
            return_value={"a.py", "b.py", "somedir", "a.py".rsplit("/", 1)[-1]},
        ):
            result = fa.attribute_failures(["tests/test_z.py::t FAILED"])
        assert result is not None
        # a.py and b.py have dots; 'somedir' does not.
        assert result["changed_files_count"] == 2

    def test_internal_error_degrades_to_unknown(self) -> None:
        """An exception inside attribution degrades all to unknown, never raises."""
        with (
            patch(f"{_MOD}.changed_files", return_value={"x.py"}),
            patch(f"{_MOD}._attribute_one", side_effect=RuntimeError("boom")),
        ):
            result = fa.attribute_failures(["tests/test_x.py::t FAILED"])
        assert result is not None
        assert result["unknown"] == 1
        assert result["per_failure"][0]["classification"] == "unknown"


class TestChangedFiles:
    def test_collects_staged_and_unstaged_paths_and_basenames(self) -> None:
        """changed_files unions HEAD + cached diffs and includes basenames."""

        def fake_run(args: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
            if "--cached" in args:
                return subprocess.CompletedProcess(args, 0, stdout="src/staged.py\n", stderr="")
            return subprocess.CompletedProcess(args, 0, stdout="src/unstaged.py\n", stderr="")

        with patch(f"{_MOD}.subprocess.run", side_effect=fake_run):
            changed = fa.changed_files()
        assert changed is not None
        assert {"src/staged.py", "staged.py", "src/unstaged.py", "unstaged.py"} <= changed

    def test_nonzero_returncode_yields_none(self) -> None:
        """A non-zero git exit (e.g. not a repo) -> None (unavailable)."""
        with patch(
            f"{_MOD}.subprocess.run",
            return_value=subprocess.CompletedProcess([], 128, stdout="", stderr="not a repo"),
        ):
            assert fa.changed_files() is None

    def test_git_missing_yields_none(self) -> None:
        """git binary absent -> None, no exception."""
        with patch(f"{_MOD}.subprocess.run", side_effect=FileNotFoundError):
            assert fa.changed_files() is None

    def test_timeout_yields_none(self) -> None:
        """git timing out -> None, no exception."""
        with patch(
            f"{_MOD}.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=15),
        ):
            assert fa.changed_files() is None


class TestBuildCheckIntegration:
    """trw_build_check surfaces failure_attribution + summary on failures."""

    def _run_tool(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        failures: list[str],
        tests_passed: bool,
    ) -> dict[str, object]:
        (tmp_path / ".trw" / "context").mkdir(parents=True)
        config = TRWConfig(build_check_enabled=True)
        monkeypatch.setattr(
            "trw_mcp.tools.build._registration.get_config", lambda: config
        )
        server = make_test_server("build")
        with (
            patch(
                "trw_mcp.tools.build._registration.resolve_trw_dir",
                return_value=tmp_path / ".trw",
            ),
            patch(
                "trw_mcp.tools.build._registration.find_active_run",
                return_value=None,
            ),
        ):
            tool = get_tools_sync(server)["trw_build_check"]
            return tool.fn(
                tests_passed=tests_passed,
                test_count=10,
                failure_count=len(failures),
                failures=failures,
                mypy_clean=True,
                scope="full",
            )

    def test_failure_surfaces_attribution_pre_existing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with patch(f"{_MOD}.changed_files", return_value={"other.py"}):
            result = self._run_tool(
                tmp_path,
                monkeypatch,
                failures=["tests/test_untouched.py::test_x FAILED"],
                tests_passed=False,
            )
        attribution = result["failure_attribution"]
        assert isinstance(attribution, dict)
        assert attribution["likely_pre_existing"] == 1
        assert "pre-existing on this tree" in str(result["summary"])

    def test_failure_surfaces_attribution_introduced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with patch(
            f"{_MOD}.changed_files",
            return_value={"tests/test_mine.py", "test_mine.py"},
        ):
            result = self._run_tool(
                tmp_path,
                monkeypatch,
                failures=["tests/test_mine.py::test_x FAILED"],
                tests_passed=False,
            )
        attribution = result["failure_attribution"]
        assert isinstance(attribution, dict)
        assert attribution["likely_introduced"] == 1
        assert "likely yours" in str(result["summary"])

    def test_passing_build_has_no_attribution(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No failures -> no failure_attribution / summary key clutter."""
        result = self._run_tool(
            tmp_path, monkeypatch, failures=[], tests_passed=True
        )
        assert "failure_attribution" not in result
        assert "summary" not in result

    def test_attribution_git_unavailable_does_not_break_tool(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """git-unavailable path: tool still returns, attribution is unknown."""
        with patch(f"{_MOD}.changed_files", return_value=None):
            result = self._run_tool(
                tmp_path,
                monkeypatch,
                failures=["tests/test_x.py::t FAILED"],
                tests_passed=False,
            )
        assert result["tests_passed"] is False
        attribution = result["failure_attribution"]
        assert isinstance(attribution, dict)
        assert attribution["unknown"] == 1
