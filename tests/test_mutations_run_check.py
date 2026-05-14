from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools.mutations import run_mutation_check

from ._mutations_support import _make_completed_process


class TestRunMutationCheck:
    """Tests for run_mutation_check."""

    @patch("trw_mcp.tools.mutations._get_changed_files", return_value=[])
    def test_returns_mutation_skipped_when_no_changed_files(self, mock_changed: MagicMock, tmp_path: Path) -> None:
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
        mock_subprocess.return_value = _make_completed_process(returncode=0, stdout='{"killed": 8, "survived": 2}')
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
