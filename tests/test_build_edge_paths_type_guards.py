"""Type-guard edge paths for build dependency audit helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._build_edge_paths_support import _make_completed_process
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools.build import _run_pip_audit


class TestPipAuditTypeGuards:
    """Tests for type guard continue-statements in _run_pip_audit parsing."""

    @patch("trw_mcp.tools.build._audit._find_executable", return_value="/usr/bin/pip-audit")
    @patch("trw_mcp.tools.build._subprocess._run_subprocess")
    def test_non_dict_dep_is_skipped(
        self,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Line 358: deps list containing non-dict entries are skipped."""
        pip_data = [
            "not-a-dict",
            42,
            None,
            {
                "name": "good-pkg",
                "version": "1.0.0",
                "vulns": [
                    {
                        "id": "CVE-2023-0001",
                        "severity": "high",
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
        assert vulns[0]["package"] == "good-pkg"

    @patch("trw_mcp.tools.build._audit._find_executable", return_value="/usr/bin/pip-audit")
    @patch("trw_mcp.tools.build._subprocess._run_subprocess")
    def test_non_list_vulns_is_skipped(
        self,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Line 361: dep with non-list 'vulns' field is skipped."""
        pip_data = [
            {
                "name": "bad-vulns-pkg",
                "version": "1.0.0",
                "vulns": "not-a-list",
            },
            {
                "name": "dict-vulns-pkg",
                "version": "2.0.0",
                "vulns": {"nested": "dict-not-list"},
            },
            {
                "name": "good-pkg",
                "version": "3.0.0",
                "vulns": [
                    {
                        "id": "CVE-2023-0002",
                        "severity": "critical",
                        "fix_versions": ["4.0.0"],
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
        assert vulns[0]["package"] == "good-pkg"

    @patch("trw_mcp.tools.build._audit._find_executable", return_value="/usr/bin/pip-audit")
    @patch("trw_mcp.tools.build._subprocess._run_subprocess")
    def test_non_dict_vuln_is_skipped(
        self,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Line 364: vulns list containing non-dict entries are skipped."""
        pip_data = [
            {
                "name": "mixed-vulns-pkg",
                "version": "1.0.0",
                "vulns": [
                    "not-a-dict",
                    42,
                    None,
                    {
                        "id": "CVE-2023-0003",
                        "severity": "high",
                        "fix_versions": ["2.0.0"],
                    },
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
        assert vulns[0]["cve_id"] == "CVE-2023-0003"
