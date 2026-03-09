"""Tests for uncovered error/edge paths in tools/build.py.

Covers:
- Lines 358, 361, 364: Type guard checks in pip-audit parsing (non-dict dep,
  non-list vulns, non-dict vuln).
- Lines 375-380: CVSS score fallback for high/medium/low severity ranges.
- Line 889: Invalid scope parameter returns error dict.
- Lines 905, 917, 924: Disabled feature flags (mutation_enabled,
  dep_audit_enabled, api_fuzz_enabled) return skipped.
- Lines 465, 472-473: npm audit JSON parse failures (JSONDecodeError, TypeError).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastmcp import FastMCP

from tests.conftest import get_tools_sync

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools.build import (
    _run_npm_audit,
    _run_pip_audit,
    register_build_tools,
)


def _make_completed_process(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> MagicMock:
    """Create a mock subprocess.CompletedProcess."""
    mock = MagicMock()
    mock.returncode = returncode
    mock.stdout = stdout
    mock.stderr = stderr
    return mock


def _get_tool_fn(server: FastMCP) -> object:
    """Extract trw_build_check tool function from a FastMCP server."""
    tools = get_tools_sync(server)
    if "trw_build_check" in tools:
        return tools["trw_build_check"].fn
    raise AssertionError("trw_build_check tool not found on server")


# ---------------------------------------------------------------------------
# Lines 358, 361, 364: Type guard checks in pip-audit parsing
# ---------------------------------------------------------------------------


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
        mock_subprocess.return_value = _make_completed_process(
            returncode=0, stdout=json.dumps(pip_data)
        )
        config = TRWConfig(dep_audit_level="high")
        result = _run_pip_audit(tmp_path, config)
        # Only the valid dict dep should produce a vulnerability
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
        mock_subprocess.return_value = _make_completed_process(
            returncode=0, stdout=json.dumps(pip_data)
        )
        config = TRWConfig(dep_audit_level="high")
        result = _run_pip_audit(tmp_path, config)
        # Only the dep with a proper list of vulns should produce results
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
        mock_subprocess.return_value = _make_completed_process(
            returncode=0, stdout=json.dumps(pip_data)
        )
        config = TRWConfig(dep_audit_level="high")
        result = _run_pip_audit(tmp_path, config)
        # Only the valid dict vuln should be counted
        assert result.get("pip_audit_vulnerability_count") == 1
        vulns = result.get("pip_audit_vulnerabilities", [])
        assert isinstance(vulns, list)
        assert len(vulns) == 1
        assert vulns[0]["cve_id"] == "CVE-2023-0003"


# ---------------------------------------------------------------------------
# Lines 375-380: CVSS score fallback for each severity tier
# ---------------------------------------------------------------------------


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
        mock_subprocess.return_value = _make_completed_process(
            returncode=0, stdout=json.dumps(pip_data)
        )
        # dep_audit_level="high" means min_rank=3; CVSS 7.5 -> rank 3 -> included
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
        mock_subprocess.return_value = _make_completed_process(
            returncode=0, stdout=json.dumps(pip_data)
        )
        # dep_audit_level="medium" means min_rank=2; CVSS 5.5 -> rank 2 -> included
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
        mock_subprocess.return_value = _make_completed_process(
            returncode=0, stdout=json.dumps(pip_data)
        )
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
        mock_subprocess.return_value = _make_completed_process(
            returncode=0, stdout=json.dumps(pip_data)
        )
        # dep_audit_level="low" means min_rank=1; CVSS 2.0 -> rank 1 -> included
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
        mock_subprocess.return_value = _make_completed_process(
            returncode=0, stdout=json.dumps(pip_data)
        )
        config = TRWConfig(dep_audit_level="high")
        result = _run_pip_audit(tmp_path, config)
        assert result.get("pip_audit_vulnerability_count") == 0


# ---------------------------------------------------------------------------
# Line 889: Invalid scope parameter
# ---------------------------------------------------------------------------


class TestInvalidScope:
    """Test that an invalid scope parameter returns an error dict."""

    @patch("trw_mcp.tools.build._registration._config")
    @patch("trw_mcp.tools.build._registration.resolve_project_root")
    @patch("trw_mcp.tools.build._registration.resolve_trw_dir")
    def test_invalid_scope_returns_error(
        self,
        mock_trw_dir: MagicMock,
        mock_proj_root: MagicMock,
        mock_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Line 889: scope='invalid' returns error dict with valid scopes listed."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        mock_trw_dir.return_value = trw_dir
        mock_proj_root.return_value = tmp_path
        mock_config.build_check_enabled = True
        mock_config.build_check_timeout_secs = 120

        server = FastMCP("test")
        register_build_tools(server)
        tool_fn = _get_tool_fn(server)

        result = tool_fn(scope="invalid")
        assert result["status"] == "error"
        assert "Invalid scope" in str(result["reason"])
        assert "'invalid'" in str(result["reason"])

    @patch("trw_mcp.tools.build._registration._config")
    @patch("trw_mcp.tools.build._registration.resolve_project_root")
    @patch("trw_mcp.tools.build._registration.resolve_trw_dir")
    def test_empty_scope_returns_error(
        self,
        mock_trw_dir: MagicMock,
        mock_proj_root: MagicMock,
        mock_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Empty string scope is also invalid."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        mock_trw_dir.return_value = trw_dir
        mock_proj_root.return_value = tmp_path
        mock_config.build_check_enabled = True
        mock_config.build_check_timeout_secs = 120

        server = FastMCP("test")
        register_build_tools(server)
        tool_fn = _get_tool_fn(server)

        result = tool_fn(scope="")
        assert result["status"] == "error"
        assert "Invalid scope" in str(result["reason"])


# ---------------------------------------------------------------------------
# Lines 905, 917, 924: Disabled feature flags
# ---------------------------------------------------------------------------


class TestDisabledFeatureFlags:
    """Tests for disabled feature flags returning skipped status."""

    @patch("trw_mcp.tools.build._registration._config")
    @patch("trw_mcp.tools.build._registration.resolve_project_root")
    @patch("trw_mcp.tools.build._registration.resolve_trw_dir")
    def test_mutations_disabled_returns_skipped(
        self,
        mock_trw_dir: MagicMock,
        mock_proj_root: MagicMock,
        mock_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Line 905: scope='mutations' with mutation_enabled=False returns skipped."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        mock_trw_dir.return_value = trw_dir
        mock_proj_root.return_value = tmp_path
        mock_config.build_check_enabled = True
        mock_config.build_check_timeout_secs = 300
        mock_config.mutation_enabled = False

        server = FastMCP("test")
        register_build_tools(server)
        tool_fn = _get_tool_fn(server)

        result = tool_fn(scope="mutations")
        assert result["status"] == "skipped"
        assert "mutation_enabled" in str(result["reason"])

    @patch("trw_mcp.tools.build._registration._config")
    @patch("trw_mcp.tools.build._registration.resolve_project_root")
    @patch("trw_mcp.tools.build._registration.resolve_trw_dir")
    def test_deps_disabled_returns_skipped(
        self,
        mock_trw_dir: MagicMock,
        mock_proj_root: MagicMock,
        mock_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Line 917: scope='deps' with dep_audit_enabled=False returns skipped."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        mock_trw_dir.return_value = trw_dir
        mock_proj_root.return_value = tmp_path
        mock_config.build_check_enabled = True
        mock_config.build_check_timeout_secs = 300
        mock_config.dep_audit_enabled = False

        server = FastMCP("test")
        register_build_tools(server)
        tool_fn = _get_tool_fn(server)

        result = tool_fn(scope="deps")
        assert result["status"] == "skipped"
        assert "dep_audit_enabled" in str(result["reason"])

    @patch("trw_mcp.tools.build._registration._config")
    @patch("trw_mcp.tools.build._registration.resolve_project_root")
    @patch("trw_mcp.tools.build._registration.resolve_trw_dir")
    def test_api_fuzz_disabled_returns_skipped(
        self,
        mock_trw_dir: MagicMock,
        mock_proj_root: MagicMock,
        mock_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Line 924: scope='api' with api_fuzz_enabled=False returns skipped."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        mock_trw_dir.return_value = trw_dir
        mock_proj_root.return_value = tmp_path
        mock_config.build_check_enabled = True
        mock_config.build_check_timeout_secs = 300
        mock_config.api_fuzz_enabled = False

        server = FastMCP("test")
        register_build_tools(server)
        tool_fn = _get_tool_fn(server)

        result = tool_fn(scope="api")
        assert result["status"] == "skipped"
        assert "api_fuzz_enabled" in str(result["reason"])


# ---------------------------------------------------------------------------
# Lines 465, 472-473: npm audit JSON parse failures
# ---------------------------------------------------------------------------


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
            returncode=1, stdout="npm ERR! audit not valid json {{{{"
        )
        config = TRWConfig()
        result = _run_npm_audit(
            tmp_path, config, changed_files=["platform/package.json"]
        )
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
        result = _run_npm_audit(
            tmp_path, config, changed_files=["platform/package.json"]
        )
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
        mock_subprocess.return_value = _make_completed_process(
            returncode=0, stdout=""
        )
        config = TRWConfig()
        result = _run_npm_audit(
            tmp_path, config, changed_files=["platform/package.json"]
        )
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
        result = _run_npm_audit(
            tmp_path, config, changed_files=["platform/package.json"]
        )
        assert result.get("npm_audit_skipped") is True
        assert "timed out" in str(result.get("npm_audit_skip_reason", ""))
