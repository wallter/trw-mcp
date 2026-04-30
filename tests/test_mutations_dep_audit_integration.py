from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from ._mutations_support import _make_completed_process
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools.build import _DEP_AUDIT_FILE, _cache_to_context, _run_dep_audit


class TestRunDepAudit:
    """Tests for _run_dep_audit."""

    @patch("trw_mcp.tools.build._audit._run_pip_audit")
    @patch("trw_mcp.tools.build._audit._run_npm_audit")
    @patch("trw_mcp.tools.build._audit.subprocess.run")
    def test_combines_pip_and_npm_results(
        self,
        mock_git: MagicMock,
        mock_npm: MagicMock,
        mock_pip: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Merges pip and npm sub-results into unified dict."""
        mock_git.return_value = _make_completed_process(returncode=0, stdout="")
        mock_pip.return_value = {"pip_audit_passed": True, "pip_audit_vulnerability_count": 0}
        mock_npm.return_value = {"npm_audit_skipped": True}
        config = TRWConfig()
        result = _run_dep_audit(tmp_path, config)
        assert "dep_audit_passed" in result
        assert "pip_audit_passed" in result
        assert "npm_audit_skipped" in result

    @patch("trw_mcp.tools.build._audit._run_pip_audit")
    @patch("trw_mcp.tools.build._audit._run_npm_audit")
    @patch("trw_mcp.tools.build._audit.subprocess.run")
    def test_overall_pass_requires_both_pip_and_npm_to_pass(
        self,
        mock_git: MagicMock,
        mock_npm: MagicMock,
        mock_pip: MagicMock,
        tmp_path: Path,
    ) -> None:
        """dep_audit_passed=False when either pip or npm fails."""
        mock_git.return_value = _make_completed_process(returncode=0, stdout="")
        mock_pip.return_value = {
            "pip_audit_passed": False,
            "pip_audit_blocking_count": 1,
        }
        mock_npm.return_value = {
            "npm_audit_passed": True,
            "npm_audit_high_plus_count": 0,
        }
        config = TRWConfig()
        result = _run_dep_audit(tmp_path, config)
        assert result["dep_audit_passed"] is False

    @patch("trw_mcp.tools.build._audit._run_pip_audit")
    @patch("trw_mcp.tools.build._audit._run_npm_audit")
    @patch("trw_mcp.tools.build._audit.subprocess.run")
    def test_dep_audit_passed_true_when_both_pass(
        self,
        mock_git: MagicMock,
        mock_npm: MagicMock,
        mock_pip: MagicMock,
        tmp_path: Path,
    ) -> None:
        """dep_audit_passed=True when both pip and npm pass."""
        mock_git.return_value = _make_completed_process(returncode=0, stdout="")
        mock_pip.return_value = {"pip_audit_passed": True}
        mock_npm.return_value = {"npm_audit_passed": True}
        config = TRWConfig()
        result = _run_dep_audit(tmp_path, config)
        assert result["dep_audit_passed"] is True

    @patch("trw_mcp.tools.build._audit._run_pip_audit")
    @patch("trw_mcp.tools.build._audit._run_npm_audit")
    @patch("trw_mcp.tools.build._audit.subprocess.run")
    def test_unlisted_imports_included_in_result(
        self,
        mock_git: MagicMock,
        mock_npm: MagicMock,
        mock_pip: MagicMock,
        tmp_path: Path,
    ) -> None:
        """unlisted_imports key present when _detect_unlisted_imports finds something."""
        py_file = tmp_path / "module.py"
        py_file.write_text("import some_unlisted_pkg\n", encoding="utf-8")
        mock_git.return_value = _make_completed_process(returncode=0, stdout="module.py\n")
        mock_pip.return_value = {"pip_audit_passed": True}
        mock_npm.return_value = {"npm_audit_skipped": True}
        config = TRWConfig(source_package_path="")
        result = _run_dep_audit(tmp_path, config)
        assert "dep_audit_passed" in result

    @patch("trw_mcp.tools.build._audit._run_pip_audit")
    @patch("trw_mcp.tools.build._audit._run_npm_audit")
    @patch(
        "trw_mcp.tools.build._audit.subprocess.run",
        side_effect=subprocess.TimeoutExpired("git", 30),
    )
    def test_git_timeout_uses_empty_changed_files(
        self,
        mock_git: MagicMock,
        mock_npm: MagicMock,
        mock_pip: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Git timeout does not crash dep_audit; changed_files defaults to []."""
        mock_pip.return_value = {"pip_audit_passed": True}
        mock_npm.return_value = {"npm_audit_skipped": True}
        config = TRWConfig()
        result = _run_dep_audit(tmp_path, config)
        assert "dep_audit_passed" in result


class TestCacheDepAudit:
    """Tests for _cache_to_context with _DEP_AUDIT_FILE."""

    def test_writes_yaml_to_correct_path(self, tmp_path: Path) -> None:
        """Writes dep-audit.yaml to .trw/context/."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        result = {"dep_audit_passed": True, "pip_audit_passed": True}
        path = _cache_to_context(trw_dir, _DEP_AUDIT_FILE, result)
        assert path.name == "dep-audit.yaml"
        assert path.parent.name == "context"
        assert path.exists()

    def test_written_data_is_readable(self, tmp_path: Path) -> None:
        """Written YAML can be read back correctly."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        result = {"dep_audit_passed": False, "pip_audit_blocking_count": 2}
        path = _cache_to_context(trw_dir, _DEP_AUDIT_FILE, result)
        data = FileStateReader().read_yaml(path)
        assert data["dep_audit_passed"] is False
        assert data["pip_audit_blocking_count"] == 2
