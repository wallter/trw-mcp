from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools.mutations import cache_mutation_status, run_mutation_check

from ._mutations_support import _make_completed_process


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
        path = cache_mutation_status(trw_dir, {"mutation_skipped": True})
        assert path.parent.exists()
        assert path.exists()


class TestRunMutationCheckEdgeCases:
    """Additional edge cases for run_mutation_check."""

    def test_uses_default_source_path_when_config_is_empty_string(self, tmp_path: Path) -> None:
        """source_package_path='' (empty string) in config → falls back to 'trw-mcp/src'."""
        config = TRWConfig(source_package_path="")
        with patch("trw_mcp.tools.mutations._get_changed_files", return_value=[]) as mock_changed:
            result = run_mutation_check(tmp_path, config)
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
