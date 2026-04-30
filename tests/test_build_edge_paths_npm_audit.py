"""npm audit parsing edge paths for build helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._build_edge_paths_support import _make_completed_process
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools.build import _run_npm_audit


class TestNpmAuditJsonParseFailures:
    """Tests for npm audit when stdout is not valid JSON or unexpected type."""

    @patch("trw_mcp.tools.build._audit.shutil.which", return_value="/usr/bin/npm")
    @patch("trw_mcp.tools.build._subprocess._run_subprocess")
    def test_npm_audit_invalid_json(
        self,
        mock_subprocess: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Line 472: JSONDecodeError when npm audit returns non-JSON output."""
        (tmp_path / "platform").mkdir()
        mock_subprocess.return_value = _make_completed_process(
            returncode=1,
            stdout="npm ERR! audit not valid json {{{{",
        )
        config = TRWConfig()
        result = _run_npm_audit(tmp_path, config, changed_files=["platform/package.json"])
        assert result.get("npm_audit_skipped") is True
        assert "invalid JSON" in str(result.get("npm_audit_skip_reason", ""))

    @patch("trw_mcp.tools.build._audit.shutil.which", return_value="/usr/bin/npm")
    @patch("trw_mcp.tools.build._subprocess._run_subprocess")
    def test_npm_audit_stdout_none_type_error(
        self,
        mock_subprocess: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Line 472-473: TypeError when npm audit stdout is None."""
        (tmp_path / "platform").mkdir()
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = None
        mock_proc.stderr = ""
        mock_subprocess.return_value = mock_proc
        config = TRWConfig()
        result = _run_npm_audit(tmp_path, config, changed_files=["platform/package.json"])
        assert result.get("npm_audit_skipped") is True
        assert "invalid JSON" in str(result.get("npm_audit_skip_reason", ""))

    @patch("trw_mcp.tools.build._audit.shutil.which", return_value="/usr/bin/npm")
    @patch("trw_mcp.tools.build._subprocess._run_subprocess")
    def test_npm_audit_empty_json_output(
        self,
        mock_subprocess: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Empty stdout is not valid JSON and should return skipped."""
        (tmp_path / "platform").mkdir()
        mock_subprocess.return_value = _make_completed_process(returncode=0, stdout="")
        config = TRWConfig()
        result = _run_npm_audit(tmp_path, config, changed_files=["platform/package.json"])
        assert result.get("npm_audit_skipped") is True
        assert "invalid JSON" in str(result.get("npm_audit_skip_reason", ""))

    @patch("trw_mcp.tools.build._audit.shutil.which", return_value="/usr/bin/npm")
    @patch(
        "trw_mcp.tools.build._subprocess._run_subprocess",
        return_value="npm timed out after 60s",
    )
    def test_npm_audit_subprocess_error_string(
        self,
        mock_subprocess: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Line 465: _run_subprocess returns error string for npm audit."""
        (tmp_path / "platform").mkdir()
        config = TRWConfig()
        result = _run_npm_audit(tmp_path, config, changed_files=["platform/package.json"])
        assert result.get("npm_audit_skipped") is True
        assert "timed out" in str(result.get("npm_audit_skip_reason", ""))
