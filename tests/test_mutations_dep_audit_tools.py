from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from ._mutations_support import _make_completed_process
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools.build import _detect_unlisted_imports, _run_npm_audit, _run_pip_audit


class TestRunPipAudit:
    """Tests for _run_pip_audit."""

    @patch("trw_mcp.tools.build._audit._find_executable", return_value=None)
    def test_skips_when_pip_audit_not_installed(self, mock_find: MagicMock, tmp_path: Path) -> None:
        """Returns pip_audit_skipped=True when pip-audit is not installed."""
        config = TRWConfig()
        result = _run_pip_audit(tmp_path, config)
        assert result.get("pip_audit_skipped") is True
        assert "not installed" in str(result.get("pip_audit_skip_reason", ""))

    @patch("trw_mcp.tools.build._audit._find_executable", return_value="/usr/bin/pip-audit")
    @patch("trw_mcp.tools.build._subprocess._run_subprocess")
    def test_parses_vulnerabilities_and_filters_by_severity(
        self,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Only vulns at or above dep_audit_level (high) are included."""
        pip_data = [
            {
                "name": "requests",
                "version": "2.28.0",
                "vulns": [
                    {
                        "id": "CVE-2023-1234",
                        "severity": "high",
                        "fix_versions": ["2.31.0"],
                    },
                    {
                        "id": "CVE-2023-5678",
                        "severity": "low",
                        "fix_versions": [],
                    },
                ],
            }
        ]
        mock_subprocess.return_value = _make_completed_process(returncode=0, stdout=json.dumps(pip_data))
        config = TRWConfig(dep_audit_level="high")
        result = _run_pip_audit(tmp_path, config)
        assert result.get("pip_audit_vulnerability_count") == 1
        vulns = result.get("pip_audit_vulnerabilities", [])
        assert isinstance(vulns, list)
        assert len(vulns) == 1
        assert vulns[0]["cve_id"] == "CVE-2023-1234"

    @patch("trw_mcp.tools.build._audit._find_executable", return_value="/usr/bin/pip-audit")
    @patch("trw_mcp.tools.build._subprocess._run_subprocess")
    def test_block_on_patchable_only_true_counts_only_fixed(
        self,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When block_on_patchable_only=True, only vulns with fix_versions block."""
        pip_data = [
            {
                "name": "cryptography",
                "version": "3.4.8",
                "vulns": [
                    {
                        "id": "CVE-2023-AAAA",
                        "severity": "high",
                        "fix_versions": ["41.0.0"],
                    },
                    {
                        "id": "CVE-2023-BBBB",
                        "severity": "high",
                        "fix_versions": [],
                    },
                ],
            }
        ]
        mock_subprocess.return_value = _make_completed_process(returncode=0, stdout=json.dumps(pip_data))
        config = TRWConfig(
            dep_audit_level="high",
            dep_audit_block_on_patchable_only=True,
        )
        result = _run_pip_audit(tmp_path, config)
        assert result.get("pip_audit_blocking_count") == 1
        assert result.get("pip_audit_passed") is False

    @patch("trw_mcp.tools.build._audit._find_executable", return_value="/usr/bin/pip-audit")
    @patch("trw_mcp.tools.build._subprocess._run_subprocess")
    def test_returns_passed_true_when_no_blocking_vulns(
        self,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        tmp_path: Path,
    ) -> None:
        """pip_audit_passed=True when no vulnerabilities meet blocking criteria."""
        mock_subprocess.return_value = _make_completed_process(returncode=0, stdout=json.dumps([]))
        config = TRWConfig()
        result = _run_pip_audit(tmp_path, config)
        assert result.get("pip_audit_passed") is True
        assert result.get("pip_audit_blocking_count") == 0

    @patch("trw_mcp.tools.build._audit._find_executable", return_value="/usr/bin/pip-audit")
    @patch(
        "trw_mcp.tools.build._subprocess._run_subprocess",
        return_value="pip-audit timed out after 30s",
    )
    def test_skips_when_subprocess_returns_error_string(
        self,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Returns pip_audit_skipped=True when subprocess returns an error string."""
        config = TRWConfig()
        result = _run_pip_audit(tmp_path, config)
        assert result.get("pip_audit_skipped") is True

    @patch("trw_mcp.tools.build._audit._find_executable", return_value="/usr/bin/pip-audit")
    @patch("trw_mcp.tools.build._subprocess._run_subprocess")
    def test_skips_on_invalid_json_output(
        self,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Returns pip_audit_skipped=True when pip-audit output is not valid JSON."""
        mock_subprocess.return_value = _make_completed_process(returncode=1, stdout="not json output")
        config = TRWConfig()
        result = _run_pip_audit(tmp_path, config)
        assert result.get("pip_audit_skipped") is True

    @patch("trw_mcp.tools.build._audit._find_executable", return_value="/usr/bin/pip-audit")
    @patch("trw_mcp.tools.build._subprocess._run_subprocess")
    def test_cvss_score_fallback_for_severity(
        self,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        tmp_path: Path,
    ) -> None:
        """CVSS score is used as severity fallback when severity field is unknown."""
        pip_data = [
            {
                "name": "somelib",
                "version": "1.0.0",
                "vulns": [
                    {
                        "id": "CVE-2023-CVSS",
                        "severity": "unknown",
                        "cvss_score": 9.5,
                        "fix_versions": ["2.0.0"],
                    }
                ],
            }
        ]
        mock_subprocess.return_value = _make_completed_process(returncode=0, stdout=json.dumps(pip_data))
        config = TRWConfig(dep_audit_level="high")
        result = _run_pip_audit(tmp_path, config)
        assert result.get("pip_audit_vulnerability_count") == 1


class TestRunNpmAudit:
    """Tests for _run_npm_audit."""

    def test_skips_when_no_platform_package_json_changes(self, tmp_path: Path) -> None:
        """Returns npm_audit_skipped=True when no platform/package.json in changeset."""
        config = TRWConfig()
        result = _run_npm_audit(tmp_path, config, changed_files=["src/mymodule.py", "tests/test_foo.py"])
        assert result.get("npm_audit_skipped") is True
        assert "no platform/package.json" in str(result.get("npm_audit_skip_reason", ""))

    @patch("trw_mcp.tools.build._audit.shutil.which", return_value=None)
    def test_skips_when_npm_not_installed(self, mock_which: MagicMock, tmp_path: Path) -> None:
        """Returns npm_audit_skipped=True when npm is not on PATH."""
        (tmp_path / "platform").mkdir()
        config = TRWConfig()
        result = _run_npm_audit(tmp_path, config, changed_files=["platform/package.json"])
        assert result.get("npm_audit_skipped") is True
        assert "npm not installed" in str(result.get("npm_audit_skip_reason", ""))

    @patch("trw_mcp.tools.build._audit.shutil.which", return_value="/usr/bin/npm")
    @patch("trw_mcp.tools.build._subprocess._run_subprocess")
    def test_parses_high_plus_vulnerabilities(
        self,
        mock_subprocess: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Counts high and critical severity npm vulnerabilities."""
        (tmp_path / "platform").mkdir()
        npm_data = {
            "vulnerabilities": {
                "axios": {"severity": "high", "via": "prototype pollution"},
                "lodash": {"severity": "critical", "via": "RCE"},
                "express": {"severity": "low", "via": "minor xss"},
            }
        }
        mock_subprocess.return_value = _make_completed_process(returncode=1, stdout=json.dumps(npm_data))
        config = TRWConfig()
        result = _run_npm_audit(tmp_path, config, changed_files=["platform/package.json"])
        assert result.get("npm_audit_high_plus_count") == 2
        assert result.get("npm_audit_passed") is False

    @patch("trw_mcp.tools.build._audit.shutil.which", return_value="/usr/bin/npm")
    @patch("trw_mcp.tools.build._subprocess._run_subprocess")
    def test_passed_true_when_no_high_plus_vulns(
        self,
        mock_subprocess: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
    ) -> None:
        """npm_audit_passed=True when no high or critical vulnerabilities."""
        (tmp_path / "platform").mkdir()
        npm_data = {
            "vulnerabilities": {
                "express": {"severity": "low", "via": "minor"},
            }
        }
        mock_subprocess.return_value = _make_completed_process(returncode=0, stdout=json.dumps(npm_data))
        config = TRWConfig()
        result = _run_npm_audit(tmp_path, config, changed_files=["platform/package.json"])
        assert result.get("npm_audit_passed") is True

    def test_skips_when_platform_dir_not_found(self, tmp_path: Path) -> None:
        """Returns npm_audit_skipped=True when platform/ directory is missing."""
        config = TRWConfig()
        result = _run_npm_audit(tmp_path, config, changed_files=["platform/package.json"])
        assert result.get("npm_audit_skipped") is True


class TestDetectUnlistedImports:
    """Tests for _detect_unlisted_imports."""

    def test_detects_imports_not_in_pyproject(self, tmp_path: Path) -> None:
        """Detects third-party import that is absent from pyproject.toml."""
        py_file = tmp_path / "mymodule.py"
        py_file.write_text("import requests\nimport os\nfrom pathlib import Path\n", encoding="utf-8")
        result = _detect_unlisted_imports(tmp_path, ["mymodule.py"])
        assert "requests" in result

    def test_excludes_stdlib_modules(self, tmp_path: Path) -> None:
        """Standard library modules are not reported as unlisted."""
        py_file = tmp_path / "mymodule.py"
        py_file.write_text(
            "import os\nimport sys\nimport json\nfrom pathlib import Path\n",
            encoding="utf-8",
        )
        result = _detect_unlisted_imports(tmp_path, ["mymodule.py"])
        for stdlib_mod in ("os", "sys", "json", "pathlib"):
            assert stdlib_mod not in result

    def test_handles_missing_files_gracefully(self, tmp_path: Path) -> None:
        """Missing files are skipped without raising an exception."""
        result = _detect_unlisted_imports(tmp_path, ["nonexistent/module.py"])
        assert result == []

    def test_listed_dep_not_flagged(self, tmp_path: Path) -> None:
        """Imports listed in pyproject.toml dependencies array are not flagged."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "myapp"\ndependencies = [\n    "requests>=2.28",\n]\n',
            encoding="utf-8",
        )
        py_file = tmp_path / "mymodule.py"
        py_file.write_text("import requests\n", encoding="utf-8")
        result = _detect_unlisted_imports(tmp_path, ["mymodule.py"])
        assert "requests" not in result

    def test_skips_dunder_private_modules(self, tmp_path: Path) -> None:
        """Imports starting with underscore are excluded."""
        py_file = tmp_path / "mymodule.py"
        py_file.write_text("import __future__\nfrom __future__ import annotations\n", encoding="utf-8")
        result = _detect_unlisted_imports(tmp_path, ["mymodule.py"])
        assert "__future__" not in result

    def test_handles_from_import_syntax(self, tmp_path: Path) -> None:
        """Both 'import X' and 'from X import Y' syntaxes are detected."""
        py_file = tmp_path / "mymodule.py"
        py_file.write_text("import numpy\nfrom pandas import DataFrame\n", encoding="utf-8")
        result = _detect_unlisted_imports(tmp_path, ["mymodule.py"])
        assert "numpy" in result
        assert "pandas" in result

    def test_empty_changed_files_returns_empty(self, tmp_path: Path) -> None:
        """Empty changed_files list returns empty list."""
        result = _detect_unlisted_imports(tmp_path, [])
        assert result == []

    def test_normalizes_hyphen_underscore(self, tmp_path: Path) -> None:
        """Package names with hyphens are normalized (- -> _) for matching."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "app"\ndependencies = ["ruamel-yaml>=0.18"]\n',
            encoding="utf-8",
        )
        py_file = tmp_path / "mymodule.py"
        py_file.write_text("import ruamel\n", encoding="utf-8")
        result = _detect_unlisted_imports(tmp_path, ["mymodule.py"])
        assert isinstance(result, list)
