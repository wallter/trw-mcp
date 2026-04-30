"""Severity-fallback and invalid-scope edge paths for build tools."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastmcp import FastMCP

from tests._build_edge_paths_support import _get_tool_fn, _make_completed_process
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools.build import _run_pip_audit, register_build_tools


class TestCvssSeverityFallback:
    """Tests for CVSS-based severity fallback when named severity is unknown."""

    @patch("trw_mcp.tools.build._audit._find_executable", return_value="/usr/bin/pip-audit")
    @patch("trw_mcp.tools.build._subprocess._run_subprocess")
    def test_cvss_high_severity_fallback(
        self,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Lines 375-376: CVSS 7.0-8.9 maps to high (rank 3), passes min_rank=3."""
        pip_data = [
            {
                "name": "highlib",
                "version": "1.0.0",
                "vulns": [
                    {
                        "id": "CVE-2023-HIGH",
                        "severity": "unknown",
                        "cvss_score": 7.5,
                        "fix_versions": ["2.0.0"],
                    }
                ],
            },
        ]
        mock_subprocess.return_value = _make_completed_process(returncode=0, stdout=json.dumps(pip_data))
        config = TRWConfig(dep_audit_level="high")
        result = _run_pip_audit(tmp_path, config)
        assert result.get("pip_audit_vulnerability_count") == 1
        vulns = result.get("pip_audit_vulnerabilities", [])
        assert isinstance(vulns, list)
        assert len(vulns) == 1
        assert vulns[0]["cvss_score"] == 7.5

    @patch("trw_mcp.tools.build._audit._find_executable", return_value="/usr/bin/pip-audit")
    @patch("trw_mcp.tools.build._subprocess._run_subprocess")
    def test_cvss_medium_severity_fallback(
        self,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Lines 377-378: CVSS 4.0-6.9 maps to medium (rank 2)."""
        pip_data = [
            {
                "name": "medlib",
                "version": "1.0.0",
                "vulns": [
                    {
                        "id": "CVE-2023-MED",
                        "severity": "unknown",
                        "cvss_score": 5.5,
                        "fix_versions": ["2.0.0"],
                    }
                ],
            },
        ]
        mock_subprocess.return_value = _make_completed_process(returncode=0, stdout=json.dumps(pip_data))
        config = TRWConfig(dep_audit_level="medium")
        result = _run_pip_audit(tmp_path, config)
        assert result.get("pip_audit_vulnerability_count") == 1
        vulns = result.get("pip_audit_vulnerabilities", [])
        assert isinstance(vulns, list)
        assert len(vulns) == 1
        assert vulns[0]["severity"] == "unknown"
        assert vulns[0]["cvss_score"] == 5.5

    @patch("trw_mcp.tools.build._audit._find_executable", return_value="/usr/bin/pip-audit")
    @patch("trw_mcp.tools.build._subprocess._run_subprocess")
    def test_cvss_medium_excluded_at_high_level(
        self,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        tmp_path: Path,
    ) -> None:
        """CVSS 5.5 maps to medium (rank 2), excluded when dep_audit_level='high' (min_rank=3)."""
        pip_data = [
            {
                "name": "medlib",
                "version": "1.0.0",
                "vulns": [
                    {
                        "id": "CVE-2023-MED2",
                        "severity": "unknown",
                        "cvss_score": 5.5,
                        "fix_versions": [],
                    }
                ],
            },
        ]
        mock_subprocess.return_value = _make_completed_process(returncode=0, stdout=json.dumps(pip_data))
        config = TRWConfig(dep_audit_level="high")
        result = _run_pip_audit(tmp_path, config)
        assert result.get("pip_audit_vulnerability_count") == 0

    @patch("trw_mcp.tools.build._audit._find_executable", return_value="/usr/bin/pip-audit")
    @patch("trw_mcp.tools.build._subprocess._run_subprocess")
    def test_cvss_low_severity_fallback(
        self,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Lines 379-380: CVSS < 4.0 maps to low (rank 1)."""
        pip_data = [
            {
                "name": "lowlib",
                "version": "1.0.0",
                "vulns": [
                    {
                        "id": "CVE-2023-LOW",
                        "severity": "unknown",
                        "cvss_score": 2.0,
                        "fix_versions": [],
                    }
                ],
            },
        ]
        mock_subprocess.return_value = _make_completed_process(returncode=0, stdout=json.dumps(pip_data))
        config = TRWConfig(dep_audit_level="low")
        result = _run_pip_audit(tmp_path, config)
        assert result.get("pip_audit_vulnerability_count") == 1
        vulns = result.get("pip_audit_vulnerabilities", [])
        assert isinstance(vulns, list)
        assert len(vulns) == 1
        assert vulns[0]["cve_id"] == "CVE-2023-LOW"

    @patch("trw_mcp.tools.build._audit._find_executable", return_value="/usr/bin/pip-audit")
    @patch("trw_mcp.tools.build._subprocess._run_subprocess")
    def test_cvss_low_excluded_at_high_level(
        self,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        tmp_path: Path,
    ) -> None:
        """CVSS 2.0 maps to low (rank 1), excluded when dep_audit_level='high' (min_rank=3)."""
        pip_data = [
            {
                "name": "lowlib",
                "version": "1.0.0",
                "vulns": [
                    {
                        "id": "CVE-2023-LOW2",
                        "severity": "unknown",
                        "cvss_score": 2.0,
                        "fix_versions": [],
                    }
                ],
            },
        ]
        mock_subprocess.return_value = _make_completed_process(returncode=0, stdout=json.dumps(pip_data))
        config = TRWConfig(dep_audit_level="high")
        result = _run_pip_audit(tmp_path, config)
        assert result.get("pip_audit_vulnerability_count") == 0


class TestInvalidScope:
    """Test that an invalid scope parameter returns an error dict."""

    @patch("trw_mcp.tools.build._registration.get_config")
    @patch("trw_mcp.tools.build._registration.resolve_project_root")
    @patch("trw_mcp.tools.build._registration.resolve_trw_dir")
    def test_invalid_scope_returns_error(
        self,
        mock_trw_dir: MagicMock,
        mock_proj_root: MagicMock,
        mock_get_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Line 889: scope='invalid' returns error dict with valid scopes listed."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        mock_trw_dir.return_value = trw_dir
        mock_proj_root.return_value = tmp_path
        mock_get_config.return_value = TRWConfig(build_check_enabled=True, build_check_timeout_secs=120)

        server = FastMCP("test")
        register_build_tools(server)
        tool_fn = _get_tool_fn(server)

        result = tool_fn(scope="invalid")
        assert result["status"] == "error"
        assert "Invalid scope" in str(result["reason"])
        assert "'invalid'" in str(result["reason"])

    @patch("trw_mcp.tools.build._registration.get_config")
    @patch("trw_mcp.tools.build._registration.resolve_project_root")
    @patch("trw_mcp.tools.build._registration.resolve_trw_dir")
    def test_empty_scope_returns_error(
        self,
        mock_trw_dir: MagicMock,
        mock_proj_root: MagicMock,
        mock_get_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Empty string scope is also invalid."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        mock_trw_dir.return_value = trw_dir
        mock_proj_root.return_value = tmp_path
        mock_get_config.return_value = TRWConfig(build_check_enabled=True, build_check_timeout_secs=120)

        server = FastMCP("test")
        register_build_tools(server)
        tool_fn = _get_tool_fn(server)

        result = tool_fn(scope="")
        assert result["status"] == "error"
        assert "Invalid scope" in str(result["reason"])
