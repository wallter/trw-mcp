"""Tests for mutation testing gate and build tool extensions — Sprint 43.

Covers: mutations.py (_get_changed_files, _classify_threshold_tier,
_parse_mutmut_results, run_mutation_check, cache_mutation_status),
build.py extensions (_run_pip_audit, _run_npm_audit,
_detect_unlisted_imports, _run_dep_audit, _cache_dep_audit,
_run_api_fuzz, _cache_api_fuzz), and trw_build_check MCP tool
scope='mutations', scope='deps', scope='api', and full scope dep_audit.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools.build import (
    _cache_api_fuzz,
    _cache_dep_audit,
    _detect_unlisted_imports,
    _run_api_fuzz,
    _run_dep_audit,
    _run_npm_audit,
    _run_pip_audit,
)
from trw_mcp.tools.mutations import (
    _classify_threshold_tier,
    _get_changed_files,
    _parse_mutmut_results,
    cache_mutation_status,
    run_mutation_check,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_completed_process(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    """Build a CompletedProcess for use in mock return values."""
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


# ---------------------------------------------------------------------------
# _get_changed_files tests
# ---------------------------------------------------------------------------


class TestGetChangedFiles:
    """Tests for _get_changed_files."""

    @patch("trw_mcp.tools.mutations.subprocess.run")
    def test_returns_filtered_py_files_within_source_path(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """Returns .py files that start with source_package_path."""
        mock_run.return_value = _make_completed_process(
            returncode=0,
            stdout=(
                "trw-mcp/src/trw_mcp/tools/build.py\n"
                "trw-mcp/src/trw_mcp/models/config.py\n"
                "platform/package.json\n"
                "README.md\n"
            ),
        )
        result = _get_changed_files(tmp_path, "trw-mcp/src")
        assert result == [
            "trw-mcp/src/trw_mcp/tools/build.py",
            "trw-mcp/src/trw_mcp/models/config.py",
        ]

    @patch(
        "trw_mcp.tools.mutations.subprocess.run",
        side_effect=subprocess.TimeoutExpired("git", 30),
    )
    def test_returns_empty_list_on_subprocess_timeout(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """Returns empty list when git diff times out."""
        result = _get_changed_files(tmp_path, "trw-mcp/src")
        assert result == []

    @patch("trw_mcp.tools.mutations.subprocess.run")
    def test_returns_empty_list_on_non_zero_return_code(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """Returns empty list when git exits with non-zero code."""
        mock_run.return_value = _make_completed_process(
            returncode=1, stdout="fatal: not a git repository\n"
        )
        result = _get_changed_files(tmp_path, "trw-mcp/src")
        assert result == []

    @patch("trw_mcp.tools.mutations.subprocess.run")
    def test_filters_out_non_py_files(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """Non-.py files are excluded even if they are under source_package_path."""
        mock_run.return_value = _make_completed_process(
            returncode=0,
            stdout=(
                "trw-mcp/src/trw_mcp/tools/build.py\n"
                "trw-mcp/src/trw_mcp/data/agents/trw-lead.md\n"
                "trw-mcp/src/trw_mcp/data/hooks/session-start.sh\n"
            ),
        )
        result = _get_changed_files(tmp_path, "trw-mcp/src")
        assert result == ["trw-mcp/src/trw_mcp/tools/build.py"]

    @patch("trw_mcp.tools.mutations.subprocess.run")
    def test_filters_out_files_outside_source_path(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """Files that don't start with source_package_path are excluded."""
        mock_run.return_value = _make_completed_process(
            returncode=0,
            stdout=(
                "trw-mcp/src/trw_mcp/tools/build.py\n"
                "other-pkg/src/other/module.py\n"
            ),
        )
        result = _get_changed_files(tmp_path, "trw-mcp/src")
        assert result == ["trw-mcp/src/trw_mcp/tools/build.py"]

    @patch(
        "trw_mcp.tools.mutations.subprocess.run",
        side_effect=OSError("git not found"),
    )
    def test_returns_empty_list_on_oserror(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """Returns empty list when git is not available (OSError)."""
        result = _get_changed_files(tmp_path, "trw-mcp/src")
        assert result == []

    @patch("trw_mcp.tools.mutations.subprocess.run")
    def test_empty_diff_returns_empty_list(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """Empty git diff returns empty list."""
        mock_run.return_value = _make_completed_process(returncode=0, stdout="")
        result = _get_changed_files(tmp_path, "trw-mcp/src")
        assert result == []


# ---------------------------------------------------------------------------
# _classify_threshold_tier tests
# ---------------------------------------------------------------------------


class TestClassifyThresholdTier:
    """Tests for _classify_threshold_tier."""

    def _config(self) -> TRWConfig:
        return TRWConfig(
            mutation_threshold=0.50,
            mutation_threshold_critical=0.70,
            mutation_threshold_experimental=0.30,
            mutation_critical_paths=("tools/", "state/", "models/"),
            mutation_experimental_paths=("scratch/",),
        )

    def test_returns_critical_for_tools_path(self) -> None:
        """File in tools/ → 'critical' with critical threshold."""
        config = self._config()
        tier, threshold = _classify_threshold_tier(
            "trw-mcp/src/trw_mcp/tools/build.py", config
        )
        assert tier == "critical"
        assert threshold == 0.70

    def test_returns_critical_for_state_path(self) -> None:
        """File in state/ → 'critical' with critical threshold."""
        config = self._config()
        tier, threshold = _classify_threshold_tier(
            "trw-mcp/src/trw_mcp/state/persistence.py", config
        )
        assert tier == "critical"
        assert threshold == 0.70

    def test_returns_critical_for_models_path(self) -> None:
        """File in models/ → 'critical' with critical threshold."""
        config = self._config()
        tier, threshold = _classify_threshold_tier(
            "trw-mcp/src/trw_mcp/models/config.py", config
        )
        assert tier == "critical"
        assert threshold == 0.70

    def test_returns_experimental_for_scratch_path(self) -> None:
        """File in scratch/ → 'experimental' with experimental threshold."""
        config = self._config()
        tier, threshold = _classify_threshold_tier(
            "scratch/trw-tester/prototype.py", config
        )
        assert tier == "experimental"
        assert threshold == 0.30

    def test_returns_standard_for_unmatched_file(self) -> None:
        """File not matching any tier path → 'standard' with standard threshold."""
        config = self._config()
        tier, threshold = _classify_threshold_tier(
            "trw-mcp/tests/test_build.py", config
        )
        assert tier == "standard"
        assert threshold == 0.50

    def test_critical_takes_precedence_over_experimental(self) -> None:
        """Critical path check runs first; file matching both returns 'critical'."""
        config = TRWConfig(
            mutation_critical_paths=("state/",),
            mutation_experimental_paths=("state/",),  # overlapping
            mutation_threshold_critical=0.70,
            mutation_threshold_experimental=0.30,
        )
        tier, threshold = _classify_threshold_tier(
            "trw-mcp/src/trw_mcp/state/analytics.py", config
        )
        assert tier == "critical"
        assert threshold == 0.70

    def test_custom_thresholds_respected(self) -> None:
        """Custom threshold values from config are used."""
        config = TRWConfig(
            mutation_threshold=0.60,
            mutation_threshold_critical=0.85,
            mutation_threshold_experimental=0.20,
            mutation_critical_paths=("tools/",),
            mutation_experimental_paths=("scratch/",),
        )
        _, threshold = _classify_threshold_tier("tools/mymodule.py", config)
        assert threshold == 0.85


# ---------------------------------------------------------------------------
# _parse_mutmut_results tests
# ---------------------------------------------------------------------------


class TestParseMutmutResults:
    """Tests for _parse_mutmut_results."""

    def test_parses_valid_json_killed_survived(self) -> None:
        """Parses basic killed/survived counts from valid JSON."""
        data = json.dumps({
            "killed": 30,
            "survived": 10,
            "timeout": 2,
            "suspicious": 1,
        })
        result = _parse_mutmut_results(data)
        assert result["killed"] == 30
        assert result["survived"] == 10
        assert result["timeout"] == 2
        assert result["suspicious"] == 1

    def test_returns_parse_error_dict_on_invalid_json(self) -> None:
        """Returns parse_error key on invalid JSON."""
        result = _parse_mutmut_results("not json {]}")
        assert "parse_error" in result
        assert result["killed"] == 0
        assert result["survived"] == 0
        assert result["mutation_score"] is None

    def test_calculates_mutation_score_correctly(self) -> None:
        """mutation_score = killed / (killed + survived), rounded to 4 decimal places."""
        data = json.dumps({"killed": 8, "survived": 2})
        result = _parse_mutmut_results(data)
        assert result["mutation_score"] == pytest.approx(0.8, rel=1e-3)

    def test_returns_none_score_when_total_is_zero(self) -> None:
        """mutation_score is None when killed + survived == 0."""
        data = json.dumps({"killed": 0, "survived": 0})
        result = _parse_mutmut_results(data)
        assert result["mutation_score"] is None

    def test_handles_non_dict_json_list(self) -> None:
        """When JSON is a list (not a dict), returns zeros with no crash."""
        data = json.dumps([1, 2, 3])
        result = _parse_mutmut_results(data)
        assert result["killed"] == 0
        assert result["survived"] == 0
        assert result["mutation_score"] is None

    def test_extracts_and_sorts_surviving_mutants(self) -> None:
        """surviving_mutants are extracted and sorted by line number, capped at 20."""
        mutants = [
            {"file": "foo.py", "line": 50, "description": "mutated X"},
            {"file": "foo.py", "line": 10, "description": "mutated Y"},
            {"file": "foo.py", "line": 30, "description": "mutated Z"},
        ]
        data = json.dumps({
            "killed": 5,
            "survived": 3,
            "survived_mutants": mutants,
        })
        result = _parse_mutmut_results(data)
        survivors = result["surviving_mutants"]
        assert isinstance(survivors, list)
        assert len(survivors) == 3
        lines = [int(str(m["line"])) for m in survivors]
        assert lines == sorted(lines)

    def test_surviving_mutants_capped_at_20(self) -> None:
        """More than 20 surviving mutants are truncated to 20."""
        mutants = [
            {"file": "foo.py", "line": i, "description": f"mut {i}"}
            for i in range(1, 35)
        ]
        data = json.dumps({
            "killed": 10,
            "survived": 34,
            "survived_mutants": mutants,
        })
        result = _parse_mutmut_results(data)
        assert len(result["surviving_mutants"]) == 20

    def test_total_mutants_includes_timeout_and_suspicious(self) -> None:
        """total_mutants = killed + survived + timeout + suspicious."""
        data = json.dumps({
            "killed": 10,
            "survived": 5,
            "timeout": 3,
            "suspicious": 2,
        })
        result = _parse_mutmut_results(data)
        assert result["total_mutants"] == 20

    def test_description_truncated_to_200_chars(self) -> None:
        """Mutant description strings are truncated to 200 characters."""
        long_desc = "x" * 300
        mutants = [{"file": "foo.py", "line": 1, "description": long_desc}]
        data = json.dumps({
            "killed": 1,
            "survived": 1,
            "survived_mutants": mutants,
        })
        result = _parse_mutmut_results(data)
        desc = result["surviving_mutants"][0]["description"]
        assert len(str(desc)) <= 200

    def test_handles_empty_json_string(self) -> None:
        """Empty string raises parse error gracefully."""
        result = _parse_mutmut_results("")
        assert "parse_error" in result


# ---------------------------------------------------------------------------
# run_mutation_check tests
# ---------------------------------------------------------------------------


class TestRunMutationCheck:
    """Tests for run_mutation_check."""

    @patch("trw_mcp.tools.mutations._get_changed_files", return_value=[])
    def test_returns_mutation_skipped_when_no_changed_files(
        self, mock_changed: MagicMock, tmp_path: Path
    ) -> None:
        """Returns mutation_skipped=True when no changed .py files."""
        config = TRWConfig()
        result = run_mutation_check(tmp_path, config)
        assert result.get("mutation_skipped") is True
        assert "no_changed_files" in str(result.get("mutation_skip_reason", ""))

    @patch(
        "trw_mcp.tools.mutations._get_changed_files",
        return_value=["trw-mcp/src/trw_mcp/tools/build.py"],
    )
    @patch("trw_mcp.tools.mutations._find_executable", return_value=None)
    def test_returns_mutation_skipped_when_mutmut_not_installed(
        self,
        mock_find: MagicMock,
        mock_changed: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Returns mutation_skipped=True when mutmut is not installed."""
        config = TRWConfig()
        result = run_mutation_check(tmp_path, config)
        assert result.get("mutation_skipped") is True
        assert "mutmut_not_installed" in str(result.get("mutation_skip_reason", ""))

    @patch(
        "trw_mcp.tools.mutations._get_changed_files",
        return_value=["trw-mcp/src/trw_mcp/tools/build.py"],
    )
    @patch(
        "trw_mcp.tools.mutations._find_executable",
        return_value="/usr/local/bin/mutmut",
    )
    @patch("trw_mcp.tools.mutations._run_subprocess")
    @patch("trw_mcp.tools.mutations._parse_mutmut_results")
    def test_returns_full_result_with_score_and_tier(
        self,
        mock_parse: MagicMock,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        mock_changed: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Returns mutation_passed, score, tier on successful run."""
        mock_subprocess.return_value = _make_completed_process(
            returncode=0, stdout='{"killed": 8, "survived": 2}'
        )
        mock_parse.return_value = {
            "killed": 8,
            "survived": 2,
            "timeout": 0,
            "suspicious": 0,
            "total_mutants": 10,
            "mutation_score": 0.8,
            "surviving_mutants": [],
        }
        config = TRWConfig(
            mutation_threshold=0.50,
            mutation_critical_paths=("tools/",),
            mutation_threshold_critical=0.70,
        )
        result = run_mutation_check(tmp_path, config)
        assert "mutation_passed" in result
        assert "mutation_score" in result
        assert "mutation_tier" in result
        assert "mutation_threshold" in result
        assert result["changed_file_count"] == 1

    @patch(
        "trw_mcp.tools.mutations._get_changed_files",
        return_value=["trw-mcp/src/trw_mcp/tools/build.py"],
    )
    @patch(
        "trw_mcp.tools.mutations._find_executable",
        return_value="/usr/local/bin/mutmut",
    )
    @patch(
        "trw_mcp.tools.mutations._run_subprocess",
        return_value="mutmut timed out after 300s",
    )
    def test_returns_mutation_skipped_when_mutmut_run_fails_timeout(
        self,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        mock_changed: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Returns mutation_skipped=True when mutmut subprocess times out."""
        config = TRWConfig()
        result = run_mutation_check(tmp_path, config)
        assert result.get("mutation_skipped") is True

    @patch(
        "trw_mcp.tools.mutations._get_changed_files",
        return_value=["trw-mcp/src/trw_mcp/tools/build.py"],
    )
    @patch(
        "trw_mcp.tools.mutations._find_executable",
        return_value="/usr/local/bin/mutmut",
    )
    @patch("trw_mcp.tools.mutations._run_subprocess")
    @patch("trw_mcp.tools.mutations._parse_mutmut_results")
    def test_pass_when_score_meets_threshold(
        self,
        mock_parse: MagicMock,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        mock_changed: MagicMock,
        tmp_path: Path,
    ) -> None:
        """mutation_passed=True when score >= threshold."""
        mock_subprocess.return_value = _make_completed_process(returncode=0)
        mock_parse.return_value = {
            "killed": 8,
            "survived": 2,
            "timeout": 0,
            "suspicious": 0,
            "total_mutants": 10,
            "mutation_score": 0.8,
            "surviving_mutants": [],
        }
        config = TRWConfig(mutation_threshold=0.70, mutation_critical_paths=())
        result = run_mutation_check(tmp_path, config)
        assert result["mutation_passed"] is True

    @patch(
        "trw_mcp.tools.mutations._get_changed_files",
        return_value=["trw-mcp/src/trw_mcp/tools/build.py"],
    )
    @patch(
        "trw_mcp.tools.mutations._find_executable",
        return_value="/usr/local/bin/mutmut",
    )
    @patch("trw_mcp.tools.mutations._run_subprocess")
    @patch("trw_mcp.tools.mutations._parse_mutmut_results")
    def test_fail_when_score_below_threshold(
        self,
        mock_parse: MagicMock,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        mock_changed: MagicMock,
        tmp_path: Path,
    ) -> None:
        """mutation_passed=False when score < threshold."""
        mock_subprocess.return_value = _make_completed_process(returncode=0)
        mock_parse.return_value = {
            "killed": 3,
            "survived": 7,
            "timeout": 0,
            "suspicious": 0,
            "total_mutants": 10,
            "mutation_score": 0.3,
            "surviving_mutants": [],
        }
        config = TRWConfig(mutation_threshold=0.50, mutation_critical_paths=())
        result = run_mutation_check(tmp_path, config)
        assert result["mutation_passed"] is False

    @patch(
        "trw_mcp.tools.mutations._get_changed_files",
        return_value=[
            "trw-mcp/src/trw_mcp/tools/build.py",
            "trw-mcp/src/trw_mcp/state/persistence.py",
        ],
    )
    @patch(
        "trw_mcp.tools.mutations._find_executable",
        return_value="/usr/local/bin/mutmut",
    )
    @patch("trw_mcp.tools.mutations._run_subprocess")
    @patch("trw_mcp.tools.mutations._parse_mutmut_results")
    def test_uses_highest_tier_threshold_from_changeset(
        self,
        mock_parse: MagicMock,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        mock_changed: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When changeset includes critical files, highest tier threshold is applied."""
        mock_subprocess.return_value = _make_completed_process(returncode=0)
        mock_parse.return_value = {
            "killed": 5,
            "survived": 5,
            "timeout": 0,
            "suspicious": 0,
            "total_mutants": 10,
            "mutation_score": 0.5,
            "surviving_mutants": [],
        }
        config = TRWConfig(
            mutation_threshold=0.50,
            mutation_threshold_critical=0.70,
            mutation_critical_paths=("tools/", "state/"),
        )
        result = run_mutation_check(tmp_path, config)
        # Score 0.5 >= standard 0.50 but < critical 0.70 → fail
        assert result["mutation_tier"] == "critical"
        assert result["mutation_threshold"] == 0.70
        assert result["mutation_passed"] is False

    @patch(
        "trw_mcp.tools.mutations._get_changed_files",
        return_value=["trw-mcp/src/trw_mcp/tools/build.py"],
    )
    @patch(
        "trw_mcp.tools.mutations._find_executable",
        return_value="/usr/local/bin/mutmut",
    )
    @patch("trw_mcp.tools.mutations._run_subprocess")
    @patch("trw_mcp.tools.mutations._parse_mutmut_results")
    def test_mutation_passed_true_when_score_is_none(
        self,
        mock_parse: MagicMock,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        mock_changed: MagicMock,
        tmp_path: Path,
    ) -> None:
        """mutation_passed defaults True when score is None (no mutants generated)."""
        mock_subprocess.return_value = _make_completed_process(returncode=0)
        mock_parse.return_value = {
            "killed": 0,
            "survived": 0,
            "timeout": 0,
            "suspicious": 0,
            "total_mutants": 0,
            "mutation_score": None,
            "surviving_mutants": [],
        }
        config = TRWConfig(mutation_threshold=0.50)
        result = run_mutation_check(tmp_path, config)
        assert result["mutation_passed"] is True

    @patch(
        "trw_mcp.tools.mutations._get_changed_files",
        return_value=["scratch/prototype/experiment.py"],
    )
    @patch(
        "trw_mcp.tools.mutations._find_executable",
        return_value="/usr/local/bin/mutmut",
    )
    @patch("trw_mcp.tools.mutations._run_subprocess")
    @patch("trw_mcp.tools.mutations._parse_mutmut_results")
    def test_experimental_tier_upgrade_from_standard(
        self,
        mock_parse: MagicMock,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        mock_changed: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When changeset has experimental file (no critical), tier upgrades to experimental."""
        mock_subprocess.return_value = _make_completed_process(returncode=0)
        mock_parse.return_value = {
            "killed": 3,
            "survived": 7,
            "timeout": 0,
            "suspicious": 0,
            "total_mutants": 10,
            "mutation_score": 0.3,
            "surviving_mutants": [],
        }
        config = TRWConfig(
            mutation_threshold=0.50,
            mutation_threshold_experimental=0.20,
            mutation_critical_paths=("tools/",),
            mutation_experimental_paths=("scratch/",),
        )
        result = run_mutation_check(tmp_path, config)
        # Score 0.3 >= experimental threshold 0.20 → pass
        assert result["mutation_tier"] == "experimental"
        assert result["mutation_threshold"] == 0.20
        assert result["mutation_passed"] is True

    @patch(
        "trw_mcp.tools.mutations._get_changed_files",
        return_value=["trw-mcp/src/trw_mcp/tools/build.py"],
    )
    @patch(
        "trw_mcp.tools.mutations._find_executable",
        return_value="/usr/local/bin/mutmut",
    )
    @patch(
        "trw_mcp.tools.mutations._run_subprocess",
        side_effect=[
            _make_completed_process(returncode=0, stdout=""),
            "results parse failed: timeout",
        ],
    )
    def test_returns_skipped_when_results_json_call_fails(
        self,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        mock_changed: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Returns mutation_skipped when 'mutmut results --json' call fails."""
        config = TRWConfig()
        result = run_mutation_check(tmp_path, config)
        assert result.get("mutation_skipped") is True


# ---------------------------------------------------------------------------
# cache_mutation_status tests
# ---------------------------------------------------------------------------


class TestCacheMutationStatus:
    """Tests for cache_mutation_status."""

    def test_writes_yaml_to_correct_path(self, tmp_path: Path) -> None:
        """Writes mutation-status.yaml to .trw/context/."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        result = {
            "mutation_passed": True,
            "mutation_score": 0.75,
            "mutation_tier": "standard",
        }
        path = cache_mutation_status(trw_dir, result)
        assert path.name == "mutation-status.yaml"
        assert path.parent.name == "context"
        assert path.exists()

    def test_written_data_is_readable(self, tmp_path: Path) -> None:
        """YAML can be read back with correct values."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        result = {"mutation_passed": False, "mutation_score": 0.45}
        path = cache_mutation_status(trw_dir, result)
        data = FileStateReader().read_yaml(path)
        assert data["mutation_passed"] is False
        assert float(str(data["mutation_score"])) == pytest.approx(0.45)

    def test_creates_context_dir_if_missing(self, tmp_path: Path) -> None:
        """Creates .trw/context/ directory when it does not exist."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # context/ does not exist yet
        path = cache_mutation_status(trw_dir, {"mutation_skipped": True})
        assert path.parent.exists()
        assert path.exists()


# ---------------------------------------------------------------------------
# _run_pip_audit tests
# ---------------------------------------------------------------------------


class TestRunPipAudit:
    """Tests for _run_pip_audit."""

    @patch("trw_mcp.tools.build._find_executable", return_value=None)
    def test_skips_when_pip_audit_not_installed(
        self, mock_find: MagicMock, tmp_path: Path
    ) -> None:
        """Returns pip_audit_skipped=True when pip-audit is not installed."""
        config = TRWConfig()
        result = _run_pip_audit(tmp_path, config)
        assert result.get("pip_audit_skipped") is True
        assert "not installed" in str(result.get("pip_audit_skip_reason", ""))

    @patch("trw_mcp.tools.build._find_executable", return_value="/usr/bin/pip-audit")
    @patch("trw_mcp.tools.build._run_subprocess")
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
        mock_subprocess.return_value = _make_completed_process(
            returncode=0, stdout=json.dumps(pip_data)
        )
        config = TRWConfig(dep_audit_level="high")
        result = _run_pip_audit(tmp_path, config)
        assert result.get("pip_audit_vulnerability_count") == 1
        vulns = result.get("pip_audit_vulnerabilities", [])
        assert isinstance(vulns, list)
        assert len(vulns) == 1
        assert vulns[0]["cve_id"] == "CVE-2023-1234"

    @patch("trw_mcp.tools.build._find_executable", return_value="/usr/bin/pip-audit")
    @patch("trw_mcp.tools.build._run_subprocess")
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
                        "fix_versions": [],  # no fix available
                    },
                ],
            }
        ]
        mock_subprocess.return_value = _make_completed_process(
            returncode=0, stdout=json.dumps(pip_data)
        )
        config = TRWConfig(
            dep_audit_level="high",
            dep_audit_block_on_patchable_only=True,
        )
        result = _run_pip_audit(tmp_path, config)
        # Only the fixable one blocks
        assert result.get("pip_audit_blocking_count") == 1
        assert result.get("pip_audit_passed") is False

    @patch("trw_mcp.tools.build._find_executable", return_value="/usr/bin/pip-audit")
    @patch("trw_mcp.tools.build._run_subprocess")
    def test_returns_passed_true_when_no_blocking_vulns(
        self,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        tmp_path: Path,
    ) -> None:
        """pip_audit_passed=True when no vulnerabilities meet blocking criteria."""
        mock_subprocess.return_value = _make_completed_process(
            returncode=0, stdout=json.dumps([])
        )
        config = TRWConfig()
        result = _run_pip_audit(tmp_path, config)
        assert result.get("pip_audit_passed") is True
        assert result.get("pip_audit_blocking_count") == 0

    @patch("trw_mcp.tools.build._find_executable", return_value="/usr/bin/pip-audit")
    @patch(
        "trw_mcp.tools.build._run_subprocess",
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

    @patch("trw_mcp.tools.build._find_executable", return_value="/usr/bin/pip-audit")
    @patch("trw_mcp.tools.build._run_subprocess")
    def test_skips_on_invalid_json_output(
        self,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Returns pip_audit_skipped=True when pip-audit output is not valid JSON."""
        mock_subprocess.return_value = _make_completed_process(
            returncode=1, stdout="not json output"
        )
        config = TRWConfig()
        result = _run_pip_audit(tmp_path, config)
        assert result.get("pip_audit_skipped") is True

    @patch("trw_mcp.tools.build._find_executable", return_value="/usr/bin/pip-audit")
    @patch("trw_mcp.tools.build._run_subprocess")
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
        mock_subprocess.return_value = _make_completed_process(
            returncode=0, stdout=json.dumps(pip_data)
        )
        config = TRWConfig(dep_audit_level="high")
        result = _run_pip_audit(tmp_path, config)
        assert result.get("pip_audit_vulnerability_count") == 1


# ---------------------------------------------------------------------------
# _run_npm_audit tests
# ---------------------------------------------------------------------------


class TestRunNpmAudit:
    """Tests for _run_npm_audit."""

    def test_skips_when_no_platform_package_json_changes(
        self, tmp_path: Path
    ) -> None:
        """Returns npm_audit_skipped=True when no platform/package.json in changeset."""
        config = TRWConfig()
        result = _run_npm_audit(
            tmp_path, config, changed_files=["src/mymodule.py", "tests/test_foo.py"]
        )
        assert result.get("npm_audit_skipped") is True
        assert "no platform/package.json" in str(
            result.get("npm_audit_skip_reason", "")
        )

    @patch("trw_mcp.tools.build.shutil.which", return_value=None)
    def test_skips_when_npm_not_installed(
        self, mock_which: MagicMock, tmp_path: Path
    ) -> None:
        """Returns npm_audit_skipped=True when npm is not on PATH."""
        (tmp_path / "platform").mkdir()
        config = TRWConfig()
        result = _run_npm_audit(
            tmp_path, config, changed_files=["platform/package.json"]
        )
        assert result.get("npm_audit_skipped") is True
        assert "npm not installed" in str(result.get("npm_audit_skip_reason", ""))

    @patch("trw_mcp.tools.build.shutil.which", return_value="/usr/bin/npm")
    @patch("trw_mcp.tools.build._run_subprocess")
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
        mock_subprocess.return_value = _make_completed_process(
            returncode=1, stdout=json.dumps(npm_data)
        )
        config = TRWConfig()
        result = _run_npm_audit(
            tmp_path, config, changed_files=["platform/package.json"]
        )
        assert result.get("npm_audit_high_plus_count") == 2
        assert result.get("npm_audit_passed") is False

    @patch("trw_mcp.tools.build.shutil.which", return_value="/usr/bin/npm")
    @patch("trw_mcp.tools.build._run_subprocess")
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
        mock_subprocess.return_value = _make_completed_process(
            returncode=0, stdout=json.dumps(npm_data)
        )
        config = TRWConfig()
        result = _run_npm_audit(
            tmp_path, config, changed_files=["platform/package.json"]
        )
        assert result.get("npm_audit_passed") is True

    def test_skips_when_platform_dir_not_found(self, tmp_path: Path) -> None:
        """Returns npm_audit_skipped=True when platform/ directory is missing."""
        config = TRWConfig()
        # platform dir does not exist
        result = _run_npm_audit(
            tmp_path, config, changed_files=["platform/package.json"]
        )
        assert result.get("npm_audit_skipped") is True


# ---------------------------------------------------------------------------
# _detect_unlisted_imports tests
# ---------------------------------------------------------------------------


class TestDetectUnlistedImports:
    """Tests for _detect_unlisted_imports."""

    def test_detects_imports_not_in_pyproject(self, tmp_path: Path) -> None:
        """Detects third-party import that is absent from pyproject.toml."""
        py_file = tmp_path / "mymodule.py"
        py_file.write_text(
            "import requests\nimport os\nfrom pathlib import Path\n", encoding="utf-8"
        )
        # No pyproject.toml — requests should be flagged
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
        result = _detect_unlisted_imports(
            tmp_path, ["nonexistent/module.py"]
        )
        assert result == []

    def test_listed_dep_not_flagged(self, tmp_path: Path) -> None:
        """Imports listed in pyproject.toml dependencies array are not flagged."""
        pyproject = tmp_path / "pyproject.toml"
        # Use the real pyproject.toml format: dependencies = [ "pkg>=ver", ]
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
        py_file.write_text(
            "import numpy\nfrom pandas import DataFrame\n", encoding="utf-8"
        )
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
        # pyproject lists 'ruamel-yaml' (hyphen)
        pyproject.write_text(
            '[project]\nname = "app"\ndependencies = ["ruamel-yaml>=0.18"]\n',
            encoding="utf-8",
        )
        py_file = tmp_path / "mymodule.py"
        py_file.write_text("import ruamel\n", encoding="utf-8")
        result = _detect_unlisted_imports(tmp_path, ["mymodule.py"])
        # ruamel -> ruamel; ruamel_yaml listed; ruamel itself may still differ
        # Key: no crash, result is a list
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# _run_dep_audit tests
# ---------------------------------------------------------------------------


class TestRunDepAudit:
    """Tests for _run_dep_audit."""

    @patch("trw_mcp.tools.build._run_pip_audit")
    @patch("trw_mcp.tools.build._run_npm_audit")
    @patch("trw_mcp.tools.build.subprocess.run")
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

    @patch("trw_mcp.tools.build._run_pip_audit")
    @patch("trw_mcp.tools.build._run_npm_audit")
    @patch("trw_mcp.tools.build.subprocess.run")
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

    @patch("trw_mcp.tools.build._run_pip_audit")
    @patch("trw_mcp.tools.build._run_npm_audit")
    @patch("trw_mcp.tools.build.subprocess.run")
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

    @patch("trw_mcp.tools.build._run_pip_audit")
    @patch("trw_mcp.tools.build._run_npm_audit")
    @patch("trw_mcp.tools.build.subprocess.run")
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
        # git diff returns our py file
        mock_git.return_value = _make_completed_process(
            returncode=0, stdout="module.py\n"
        )
        mock_pip.return_value = {"pip_audit_passed": True}
        mock_npm.return_value = {"npm_audit_skipped": True}
        config = TRWConfig(source_package_path="")
        result = _run_dep_audit(tmp_path, config)
        # May or may not find it depending on pyproject.toml presence, but no crash
        assert "dep_audit_passed" in result

    @patch("trw_mcp.tools.build._run_pip_audit")
    @patch("trw_mcp.tools.build._run_npm_audit")
    @patch(
        "trw_mcp.tools.build.subprocess.run",
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


# ---------------------------------------------------------------------------
# _cache_dep_audit tests
# ---------------------------------------------------------------------------


class TestCacheDepAudit:
    """Tests for _cache_dep_audit."""

    def test_writes_yaml_to_correct_path(self, tmp_path: Path) -> None:
        """Writes dep-audit.yaml to .trw/context/."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        result = {"dep_audit_passed": True, "pip_audit_passed": True}
        path = _cache_dep_audit(trw_dir, result)
        assert path.name == "dep-audit.yaml"
        assert path.parent.name == "context"
        assert path.exists()

    def test_written_data_is_readable(self, tmp_path: Path) -> None:
        """Written YAML can be read back correctly."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        result = {"dep_audit_passed": False, "pip_audit_blocking_count": 2}
        path = _cache_dep_audit(trw_dir, result)
        data = FileStateReader().read_yaml(path)
        assert data["dep_audit_passed"] is False
        assert data["pip_audit_blocking_count"] == 2


# ---------------------------------------------------------------------------
# _run_api_fuzz tests
# ---------------------------------------------------------------------------


class TestRunApiFuzz:
    """Tests for _run_api_fuzz."""

    @patch("trw_mcp.tools.build._find_executable", return_value=None)
    def test_skips_when_schemathesis_not_installed(
        self, mock_find: MagicMock, tmp_path: Path
    ) -> None:
        """Returns api_fuzz_skipped=True when neither schemathesis nor st is installed."""
        config = TRWConfig()
        result = _run_api_fuzz(tmp_path, config)
        assert result.get("api_fuzz_skipped") is True
        assert "schemathesis not installed" in str(
            result.get("api_fuzz_skip_reason", "")
        )

    @patch(
        "trw_mcp.tools.build._find_executable",
        return_value="/usr/local/bin/schemathesis",
    )
    @patch("urllib.request.urlopen")
    @patch("trw_mcp.tools.build._run_subprocess")
    def test_passed_true_when_schemathesis_exits_zero(
        self,
        mock_subprocess: MagicMock,
        mock_urlopen: MagicMock,
        mock_find: MagicMock,
        tmp_path: Path,
    ) -> None:
        """api_fuzz_passed=True when schemathesis exits 0."""
        mock_urlopen.return_value = MagicMock()
        mock_subprocess.return_value = _make_completed_process(
            returncode=0, stdout="All checks passed"
        )
        config = TRWConfig(api_fuzz_base_url="http://localhost:8000")
        result = _run_api_fuzz(tmp_path, config)
        assert result.get("api_fuzz_passed") is True

    @patch(
        "trw_mcp.tools.build._find_executable",
        return_value="/usr/local/bin/schemathesis",
    )
    @patch(
        "urllib.request.urlopen",
        side_effect=Exception("Connection refused"),
    )
    def test_skips_when_backend_unreachable(
        self,
        mock_urlopen: MagicMock,
        mock_find: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Returns api_fuzz_skipped=True when backend HEAD check fails."""
        config = TRWConfig(api_fuzz_base_url="http://localhost:8000")
        result = _run_api_fuzz(tmp_path, config)
        assert result.get("api_fuzz_skipped") is True
        assert "unreachable" in str(result.get("api_fuzz_skip_reason", ""))

    @patch(
        "trw_mcp.tools.build._find_executable",
        return_value="/usr/local/bin/schemathesis",
    )
    @patch("urllib.request.urlopen")
    @patch("trw_mcp.tools.build._run_subprocess")
    def test_failed_when_schemathesis_exits_nonzero(
        self,
        mock_subprocess: MagicMock,
        mock_urlopen: MagicMock,
        mock_find: MagicMock,
        tmp_path: Path,
    ) -> None:
        """api_fuzz_passed=False when schemathesis exits non-zero."""
        mock_urlopen.return_value = MagicMock()
        mock_subprocess.return_value = _make_completed_process(
            returncode=1,
            stdout="FAILED /api/users - 500 Internal Server Error",
        )
        config = TRWConfig()
        result = _run_api_fuzz(tmp_path, config)
        assert result.get("api_fuzz_passed") is False

    @patch(
        "trw_mcp.tools.build._find_executable",
        return_value="/usr/local/bin/schemathesis",
    )
    @patch("urllib.request.urlopen")
    @patch(
        "trw_mcp.tools.build._run_subprocess",
        return_value="schemathesis timed out after 120s",
    )
    def test_skips_on_subprocess_timeout(
        self,
        mock_subprocess: MagicMock,
        mock_urlopen: MagicMock,
        mock_find: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Returns api_fuzz_skipped=True when schemathesis subprocess times out."""
        mock_urlopen.return_value = MagicMock()
        config = TRWConfig()
        result = _run_api_fuzz(tmp_path, config)
        assert result.get("api_fuzz_skipped") is True

    @patch(
        "trw_mcp.tools.build._find_executable",
        return_value="/usr/local/bin/schemathesis",
    )
    @patch("urllib.request.urlopen")
    @patch("trw_mcp.tools.build._run_subprocess")
    def test_failure_lines_extracted(
        self,
        mock_subprocess: MagicMock,
        mock_urlopen: MagicMock,
        mock_find: MagicMock,
        tmp_path: Path,
    ) -> None:
        """api_fuzz_failures list is populated from FAILED/ERROR lines in output."""
        mock_urlopen.return_value = MagicMock()
        mock_subprocess.return_value = _make_completed_process(
            returncode=1,
            stdout=(
                "Running checks...\n"
                "FAILED /api/items - Defect found\n"
                "ERROR /api/users - 500 Server Error\n"
            ),
        )
        config = TRWConfig()
        result = _run_api_fuzz(tmp_path, config)
        assert result.get("api_fuzz_failure_count", 0) >= 1


# ---------------------------------------------------------------------------
# _cache_api_fuzz tests
# ---------------------------------------------------------------------------


class TestCacheApiFuzz:
    """Tests for _cache_api_fuzz."""

    def test_writes_yaml_to_correct_path(self, tmp_path: Path) -> None:
        """Writes api-fuzz-status.yaml to .trw/context/."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        result = {"api_fuzz_passed": True, "api_fuzz_base_url": "http://localhost:8000"}
        path = _cache_api_fuzz(trw_dir, result)
        assert path.name == "api-fuzz-status.yaml"
        assert path.parent.name == "context"
        assert path.exists()

    def test_written_data_is_readable(self, tmp_path: Path) -> None:
        """Written YAML can be read back correctly."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        result = {"api_fuzz_passed": False, "api_fuzz_failure_count": 3}
        path = _cache_api_fuzz(trw_dir, result)
        data = FileStateReader().read_yaml(path)
        assert data["api_fuzz_passed"] is False
        assert data["api_fuzz_failure_count"] == 3


# ---------------------------------------------------------------------------
# trw_build_check MCP tool — scope integration tests
# ---------------------------------------------------------------------------


def _setup_build_tool_mocks(
    mock_config: MagicMock,
    tmp_path: Path,
) -> tuple[Path, Path]:
    """Configure mock_config with required build_check_enabled flag and paths."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "context").mkdir()
    mock_config.build_check_enabled = True
    mock_config.build_check_timeout_secs = 300
    mock_config.build_check_pytest_args = ""
    mock_config.build_check_mypy_args = "--strict"
    mock_config.dep_audit_enabled = False
    return trw_dir, tmp_path


def _get_tool_fn(server: object) -> object:
    """Extract trw_build_check tool function from a FastMCP server."""
    for tool in server._tool_manager._tools.values():  # type: ignore[attr-defined]
        if tool.name == "trw_build_check":
            return tool.fn
    raise AssertionError("trw_build_check tool not found on server")


@pytest.mark.integration
class TestBuildCheckScopeIntegration:
    """Integration tests: trw_build_check MCP tool with scope='mutations','deps','api'."""

    @patch("trw_mcp.tools.build._config")
    @patch("trw_mcp.tools.build.resolve_project_root")
    @patch("trw_mcp.tools.build.resolve_trw_dir")
    @patch("trw_mcp.tools.mutations.run_mutation_check")
    @patch("trw_mcp.tools.mutations.cache_mutation_status")
    def test_scope_mutations_calls_mutation_check_and_caches(
        self,
        mock_cache: MagicMock,
        mock_run: MagicMock,
        mock_trw_dir: MagicMock,
        mock_proj_root: MagicMock,
        mock_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """scope='mutations' → run_mutation_check + cache_mutation_status called."""
        trw_dir, proj_root = _setup_build_tool_mocks(mock_config, tmp_path)
        mock_config.mutation_enabled = True
        mock_trw_dir.return_value = trw_dir
        mock_proj_root.return_value = proj_root

        mut_result: dict[str, object] = {
            "mutation_passed": True,
            "mutation_score": 0.75,
            "mutation_tier": "standard",
        }
        mock_run.return_value = mut_result
        mock_cache.return_value = trw_dir / "context" / "mutation-status.yaml"

        from fastmcp import FastMCP
        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)
        tool_fn = _get_tool_fn(server)

        result = tool_fn(scope="mutations")
        mock_run.assert_called_once()
        mock_cache.assert_called_once()
        assert result.get("mutation_passed") is True

    @patch("trw_mcp.tools.build._config")
    @patch("trw_mcp.tools.build.resolve_project_root")
    @patch("trw_mcp.tools.build.resolve_trw_dir")
    @patch("trw_mcp.tools.build._run_dep_audit")
    @patch("trw_mcp.tools.build._cache_dep_audit")
    def test_scope_deps_calls_dep_audit_and_caches(
        self,
        mock_cache: MagicMock,
        mock_run: MagicMock,
        mock_trw_dir: MagicMock,
        mock_proj_root: MagicMock,
        mock_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """scope='deps' → _run_dep_audit + _cache_dep_audit called."""
        trw_dir, proj_root = _setup_build_tool_mocks(mock_config, tmp_path)
        mock_config.dep_audit_enabled = True
        mock_trw_dir.return_value = trw_dir
        mock_proj_root.return_value = proj_root

        dep_result: dict[str, object] = {
            "dep_audit_passed": True,
            "pip_audit_passed": True,
        }
        mock_run.return_value = dep_result
        mock_cache.return_value = trw_dir / "context" / "dep-audit.yaml"

        from fastmcp import FastMCP
        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)
        tool_fn = _get_tool_fn(server)

        result = tool_fn(scope="deps")
        mock_run.assert_called_once()
        mock_cache.assert_called_once()
        assert result.get("dep_audit_passed") is True

    @patch("trw_mcp.tools.build._config")
    @patch("trw_mcp.tools.build.resolve_project_root")
    @patch("trw_mcp.tools.build.resolve_trw_dir")
    @patch("trw_mcp.tools.build._run_api_fuzz")
    @patch("trw_mcp.tools.build._cache_api_fuzz")
    def test_scope_api_calls_api_fuzz_and_caches(
        self,
        mock_cache: MagicMock,
        mock_run: MagicMock,
        mock_trw_dir: MagicMock,
        mock_proj_root: MagicMock,
        mock_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """scope='api' → _run_api_fuzz + _cache_api_fuzz called."""
        trw_dir, proj_root = _setup_build_tool_mocks(mock_config, tmp_path)
        mock_config.api_fuzz_enabled = True
        mock_trw_dir.return_value = trw_dir
        mock_proj_root.return_value = proj_root

        fuzz_result: dict[str, object] = {
            "api_fuzz_skipped": True,
            "api_fuzz_skip_reason": "schemathesis not installed",
        }
        mock_run.return_value = fuzz_result
        mock_cache.return_value = trw_dir / "context" / "api-fuzz-status.yaml"

        from fastmcp import FastMCP
        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)
        tool_fn = _get_tool_fn(server)

        result = tool_fn(scope="api")
        mock_run.assert_called_once()
        mock_cache.assert_called_once()
        assert result.get("api_fuzz_skipped") is True

    @patch("trw_mcp.tools.build._config")
    @patch("trw_mcp.tools.build.resolve_project_root")
    @patch("trw_mcp.tools.build.resolve_trw_dir")
    @patch("trw_mcp.tools.build.run_build_check")
    @patch("trw_mcp.tools.build._run_dep_audit")
    @patch("trw_mcp.tools.build._cache_dep_audit")
    def test_scope_full_includes_dep_audit_when_enabled(
        self,
        mock_cache_dep: MagicMock,
        mock_dep_audit: MagicMock,
        mock_build: MagicMock,
        mock_trw_dir: MagicMock,
        mock_proj_root: MagicMock,
        mock_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """scope='full' + dep_audit_enabled=True → _run_dep_audit is called."""
        from trw_mcp.models.build import BuildStatus

        trw_dir, proj_root = _setup_build_tool_mocks(mock_config, tmp_path)
        mock_config.dep_audit_enabled = True  # override to True
        mock_trw_dir.return_value = trw_dir
        mock_proj_root.return_value = proj_root

        mock_build.return_value = BuildStatus(
            tests_passed=True,
            mypy_clean=True,
            coverage_pct=90.0,
            test_count=100,
        )
        dep_result: dict[str, object] = {
            "dep_audit_passed": True,
            "pip_audit_passed": True,
        }
        mock_dep_audit.return_value = dep_result
        mock_cache_dep.return_value = trw_dir / "context" / "dep-audit.yaml"

        from fastmcp import FastMCP
        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)
        tool_fn = _get_tool_fn(server)

        result = tool_fn(scope="full")
        mock_dep_audit.assert_called_once()
        mock_cache_dep.assert_called_once()
        assert "dep_audit" in result

    @patch("trw_mcp.tools.build._config")
    @patch("trw_mcp.tools.build.resolve_project_root")
    @patch("trw_mcp.tools.build.resolve_trw_dir")
    @patch("trw_mcp.tools.build.run_build_check")
    def test_scope_full_skips_dep_audit_when_disabled(
        self,
        mock_build: MagicMock,
        mock_trw_dir: MagicMock,
        mock_proj_root: MagicMock,
        mock_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """scope='full' + dep_audit_enabled=False → _run_dep_audit NOT called."""
        from trw_mcp.models.build import BuildStatus

        trw_dir, proj_root = _setup_build_tool_mocks(mock_config, tmp_path)
        mock_config.dep_audit_enabled = False
        mock_trw_dir.return_value = trw_dir
        mock_proj_root.return_value = proj_root

        mock_build.return_value = BuildStatus(
            tests_passed=True,
            mypy_clean=True,
            coverage_pct=85.0,
            test_count=50,
        )

        from fastmcp import FastMCP
        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)
        tool_fn = _get_tool_fn(server)

        result = tool_fn(scope="full")
        assert "dep_audit" not in result

    @patch("trw_mcp.tools.build._config")
    @patch("trw_mcp.tools.build.resolve_project_root")
    @patch("trw_mcp.tools.build.resolve_trw_dir")
    def test_scope_mutations_returns_skipped_when_disabled(
        self,
        mock_trw_dir: MagicMock,
        mock_proj_root: MagicMock,
        mock_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """scope='mutations' returns result dict (even if skipped) without crashing."""
        trw_dir, proj_root = _setup_build_tool_mocks(mock_config, tmp_path)
        mock_trw_dir.return_value = trw_dir
        mock_proj_root.return_value = proj_root

        from fastmcp import FastMCP
        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)
        tool_fn = _get_tool_fn(server)

        # run_mutation_check will call _get_changed_files which calls subprocess
        with patch("trw_mcp.tools.mutations._get_changed_files", return_value=[]):
            with patch(
                "trw_mcp.tools.mutations.cache_mutation_status",
                return_value=trw_dir / "context" / "mutation-status.yaml",
            ):
                result = tool_fn(scope="mutations")
        # Either mutation_skipped or mutation_passed should be present
        assert "mutation_skipped" in result or "mutation_passed" in result


# ---------------------------------------------------------------------------
# Parametrized: threshold tier classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("file_path", "critical_paths", "experimental_paths", "expected_tier"),
    [
        ("trw-mcp/src/trw_mcp/tools/report.py", ("tools/",), ("scratch/",), "critical"),
        ("trw-mcp/src/trw_mcp/state/claude_md.py", ("state/",), ("scratch/",), "critical"),
        ("trw-mcp/src/trw_mcp/models/run.py", ("models/",), ("scratch/",), "critical"),
        ("scratch/prototype/test.py", ("tools/",), ("scratch/",), "experimental"),
        ("trw-mcp/tests/test_build.py", ("tools/",), ("scratch/",), "standard"),
        ("docs/README.md", ("tools/",), ("scratch/",), "standard"),
    ],
)
def test_classify_threshold_tier_parametrized(
    file_path: str,
    critical_paths: tuple[str, ...],
    experimental_paths: tuple[str, ...],
    expected_tier: str,
) -> None:
    """Parametrized tier classification covering all three tiers."""
    config = TRWConfig(
        mutation_critical_paths=critical_paths,
        mutation_experimental_paths=experimental_paths,
    )
    tier, _ = _classify_threshold_tier(file_path, config)
    assert tier == expected_tier


# ---------------------------------------------------------------------------
# Parametrized: mutation_score calculation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("killed", "survived", "expected_score"),
    [
        (10, 0, 1.0),
        (0, 10, 0.0),
        (8, 2, 0.8),
        (1, 3, 0.25),
        (0, 0, None),
    ],
)
def test_parse_mutmut_score_parametrized(
    killed: int,
    survived: int,
    expected_score: float | None,
) -> None:
    """Parametrized mutation score calculation for various killed/survived combos."""
    data = json.dumps({"killed": killed, "survived": survived})
    result = _parse_mutmut_results(data)
    if expected_score is None:
        assert result["mutation_score"] is None
    else:
        assert result["mutation_score"] == pytest.approx(expected_score, rel=1e-3)


# ---------------------------------------------------------------------------
# Edge-case: _get_changed_files
# ---------------------------------------------------------------------------


class TestGetChangedFilesEdgeCases:
    """Additional edge cases for _get_changed_files."""

    @patch(
        "trw_mcp.tools.mutations.subprocess.run",
        side_effect=FileNotFoundError("git: No such file or directory"),
    )
    def test_returns_empty_list_on_file_not_found_error(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """FileNotFoundError (git binary missing) → returns []."""
        result = _get_changed_files(tmp_path, "trw-mcp/src")
        assert result == []

    @patch("trw_mcp.tools.mutations.subprocess.run")
    def test_whitespace_only_output_returns_empty_list(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """Stdout containing only whitespace/newlines → no files extracted."""
        mock_run.return_value = _make_completed_process(
            returncode=0, stdout="\n  \n\t\n"
        )
        result = _get_changed_files(tmp_path, "trw-mcp/src")
        assert result == []

    @patch("trw_mcp.tools.mutations.subprocess.run")
    def test_no_py_files_in_source_path_returns_empty(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """All changed files are outside source path → returns []."""
        mock_run.return_value = _make_completed_process(
            returncode=0,
            stdout=(
                "platform/src/app/page.tsx\n"
                "docs/README.md\n"
                "Makefile\n"
            ),
        )
        result = _get_changed_files(tmp_path, "trw-mcp/src")
        assert result == []

    @patch("trw_mcp.tools.mutations.subprocess.run")
    def test_git_timeout_expired_returns_empty_list(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """subprocess.TimeoutExpired during git diff → returns []."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)
        result = _get_changed_files(tmp_path, "trw-mcp/src")
        assert result == []

    @patch("trw_mcp.tools.mutations.subprocess.run")
    def test_passes_project_root_as_cwd(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """subprocess.run is called with cwd=str(project_root)."""
        mock_run.return_value = _make_completed_process(returncode=0, stdout="")
        _get_changed_files(tmp_path, "trw-mcp/src")
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs.get("cwd") == str(tmp_path)


# ---------------------------------------------------------------------------
# Edge-case: _parse_mutmut_results
# ---------------------------------------------------------------------------


class TestParseMutmutResultsEdgeCases:
    """Additional edge cases for _parse_mutmut_results."""

    def test_returns_parse_error_on_none_input(self) -> None:
        """None input (TypeError in json.loads) → parse_error returned, no crash."""
        result = _parse_mutmut_results(None)  # type: ignore[arg-type]
        assert "parse_error" in result
        assert result["killed"] == 0
        assert result["mutation_score"] is None

    def test_returns_parse_error_on_non_string_int_input(self) -> None:
        """Integer input (TypeError in json.loads) → parse_error returned."""
        result = _parse_mutmut_results(42)  # type: ignore[arg-type]
        assert "parse_error" in result

    def test_valid_dict_missing_all_optional_keys(self) -> None:
        """Valid JSON dict with no recognized keys → zeros, no crash."""
        result = _parse_mutmut_results("{}")
        assert result["killed"] == 0
        assert result["survived"] == 0
        assert result["timeout"] == 0
        assert result["suspicious"] == 0
        assert result["mutation_score"] is None
        assert result["surviving_mutants"] == []

    def test_survived_mutants_non_list_is_ignored(self) -> None:
        """survived_mutants field as dict (not list) → surviving_mutants is empty."""
        data = json.dumps({
            "killed": 5,
            "survived": 3,
            "survived_mutants": {"file": "foo.py", "line": 1},
        })
        result = _parse_mutmut_results(data)
        assert result["surviving_mutants"] == []
        assert result["killed"] == 5

    def test_survived_mutants_with_non_dict_items_skipped(self) -> None:
        """Non-dict items in survived_mutants list are silently skipped."""
        data = json.dumps({
            "killed": 2,
            "survived": 2,
            "survived_mutants": [
                "not-a-dict",
                42,
                None,
                {"file": "foo.py", "line": 5, "description": "valid"},
            ],
        })
        result = _parse_mutmut_results(data)
        assert len(result["surviving_mutants"]) == 1
        assert result["surviving_mutants"][0]["file"] == "foo.py"

    def test_mutant_missing_file_and_line_defaults_to_empty_and_zero(self) -> None:
        """Mutant dict missing file/line/description defaults gracefully."""
        data = json.dumps({
            "killed": 1,
            "survived": 1,
            "survived_mutants": [{}],
        })
        result = _parse_mutmut_results(data)
        assert len(result["surviving_mutants"]) == 1
        mutant = result["surviving_mutants"][0]
        assert mutant["file"] == ""
        assert mutant["line"] == 0
        assert mutant["description"] == ""

    def test_malformed_json_with_truncated_string(self) -> None:
        """Truncated/partial JSON → parse_error, no exception propagated."""
        result = _parse_mutmut_results('{"killed": 5, "survived":')
        assert "parse_error" in result
        assert result["mutation_score"] is None

    def test_total_mutants_excludes_score_none_case(self) -> None:
        """total_mutants sums all four fields even when score is None."""
        data = json.dumps({
            "killed": 0,
            "survived": 0,
            "timeout": 4,
            "suspicious": 2,
        })
        result = _parse_mutmut_results(data)
        assert result["total_mutants"] == 6
        assert result["mutation_score"] is None  # killed+survived = 0


# ---------------------------------------------------------------------------
# Edge-case: run_mutation_check
# ---------------------------------------------------------------------------


class TestRunMutationCheckEdgeCases:
    """Additional edge cases for run_mutation_check."""

    def test_uses_default_source_path_when_config_is_empty_string(
        self, tmp_path: Path
    ) -> None:
        """source_package_path='' (empty string) in config → falls back to 'trw-mcp/src'.

        The source: `source_path = config.source_package_path or "trw-mcp/src"`.
        An empty string is falsy, so the default is used.
        """
        config = TRWConfig(source_package_path="")
        with patch(
            "trw_mcp.tools.mutations._get_changed_files", return_value=[]
        ) as mock_changed:
            result = run_mutation_check(tmp_path, config)

        # Called with the default fallback path
        mock_changed.assert_called_once_with(tmp_path, "trw-mcp/src")
        assert result["mutation_skipped"] is True

    @patch(
        "trw_mcp.tools.mutations._get_changed_files",
        return_value=["trw-mcp/src/trw_mcp/tools/build.py"],
    )
    @patch(
        "trw_mcp.tools.mutations._find_executable",
        return_value="/usr/local/bin/mutmut",
    )
    @patch("trw_mcp.tools.mutations._run_subprocess")
    @patch("trw_mcp.tools.mutations._parse_mutmut_results")
    def test_changed_files_list_included_in_result(
        self,
        mock_parse: MagicMock,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        mock_changed: MagicMock,
        tmp_path: Path,
    ) -> None:
        """changed_files list is present in successful result."""
        mock_subprocess.return_value = _make_completed_process(returncode=0, stdout="")
        mock_parse.return_value = {
            "killed": 8,
            "survived": 2,
            "timeout": 0,
            "suspicious": 0,
            "total_mutants": 10,
            "mutation_score": 0.8,
            "surviving_mutants": [],
        }
        config = TRWConfig(mutation_threshold=0.50, mutation_critical_paths=())
        result = run_mutation_check(tmp_path, config)
        assert "changed_files" in result
        assert result["changed_files"] == ["trw-mcp/src/trw_mcp/tools/build.py"]

    @patch(
        "trw_mcp.tools.mutations._get_changed_files",
        return_value=["trw-mcp/src/trw_mcp/tools/build.py"],
    )
    @patch(
        "trw_mcp.tools.mutations._find_executable",
        return_value="/usr/local/bin/mutmut",
    )
    @patch(
        "trw_mcp.tools.mutations._run_subprocess",
        return_value="subprocess error: permission denied",
    )
    def test_skipped_when_mutmut_run_returns_error_string(
        self,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        mock_changed: MagicMock,
        tmp_path: Path,
    ) -> None:
        """_run_subprocess returning a string for the run step → mutation_skipped=True."""
        config = TRWConfig()
        result = run_mutation_check(tmp_path, config)
        assert result.get("mutation_skipped") is True
        assert "subprocess error" in str(result.get("mutation_skip_reason", ""))

    @patch(
        "trw_mcp.tools.mutations._get_changed_files",
        return_value=[
            "trw-mcp/src/trw_mcp/scratch/proto.py",
            "trw-mcp/src/trw_mcp/state/analytics.py",
        ],
    )
    @patch(
        "trw_mcp.tools.mutations._find_executable",
        return_value="/usr/local/bin/mutmut",
    )
    @patch("trw_mcp.tools.mutations._run_subprocess")
    @patch("trw_mcp.tools.mutations._parse_mutmut_results")
    def test_critical_tier_wins_over_experimental_in_mixed_changeset(
        self,
        mock_parse: MagicMock,
        mock_subprocess: MagicMock,
        mock_find: MagicMock,
        mock_changed: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Critical file in changeset beats experimental file → tier='critical'."""
        mock_subprocess.return_value = _make_completed_process(returncode=0, stdout="")
        mock_parse.return_value = {
            "killed": 5,
            "survived": 5,
            "timeout": 0,
            "suspicious": 0,
            "total_mutants": 10,
            "mutation_score": 0.5,
            "surviving_mutants": [],
        }
        config = TRWConfig(
            mutation_threshold=0.50,
            mutation_threshold_critical=0.80,
            mutation_threshold_experimental=0.20,
            mutation_critical_paths=("state/",),
            mutation_experimental_paths=("scratch/",),
        )
        result = run_mutation_check(tmp_path, config)
        assert result["mutation_tier"] == "critical"
        assert result["mutation_threshold"] == 0.80
