from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools.mutations import _classify_threshold_tier, _get_changed_files

from ._mutations_support import _make_completed_process


class TestGetChangedFiles:
    """Tests for _get_changed_files."""

    @patch("trw_mcp.tools.mutations.subprocess.run")
    def test_returns_filtered_py_files_within_source_path(self, mock_run: MagicMock, tmp_path: Path) -> None:
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
    def test_returns_empty_list_on_subprocess_timeout(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """Returns empty list when git diff times out."""
        result = _get_changed_files(tmp_path, "trw-mcp/src")
        assert result == []

    @patch("trw_mcp.tools.mutations.subprocess.run")
    def test_returns_empty_list_on_non_zero_return_code(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """Returns empty list when git exits with non-zero code."""
        mock_run.return_value = _make_completed_process(returncode=1, stdout="fatal: not a git repository\n")
        result = _get_changed_files(tmp_path, "trw-mcp/src")
        assert result == []

    @patch("trw_mcp.tools.mutations.subprocess.run")
    def test_filters_out_non_py_files(self, mock_run: MagicMock, tmp_path: Path) -> None:
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
    def test_filters_out_files_outside_source_path(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """Files that don't start with source_package_path are excluded."""
        mock_run.return_value = _make_completed_process(
            returncode=0,
            stdout=("trw-mcp/src/trw_mcp/tools/build.py\nother-pkg/src/other/module.py\n"),
        )
        result = _get_changed_files(tmp_path, "trw-mcp/src")
        assert result == ["trw-mcp/src/trw_mcp/tools/build.py"]

    @patch(
        "trw_mcp.tools.mutations.subprocess.run",
        side_effect=OSError("git not found"),
    )
    def test_returns_empty_list_on_oserror(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """Returns empty list when git is not available (OSError)."""
        result = _get_changed_files(tmp_path, "trw-mcp/src")
        assert result == []

    @patch("trw_mcp.tools.mutations.subprocess.run")
    def test_empty_diff_returns_empty_list(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """Empty git diff returns empty list."""
        mock_run.return_value = _make_completed_process(returncode=0, stdout="")
        result = _get_changed_files(tmp_path, "trw-mcp/src")
        assert result == []


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
        tier, threshold = _classify_threshold_tier("trw-mcp/src/trw_mcp/tools/build.py", config)
        assert tier == "critical"
        assert threshold == 0.70

    def test_returns_critical_for_state_path(self) -> None:
        """File in state/ → 'critical' with critical threshold."""
        config = self._config()
        tier, threshold = _classify_threshold_tier("trw-mcp/src/trw_mcp/state/persistence.py", config)
        assert tier == "critical"
        assert threshold == 0.70

    def test_returns_critical_for_models_path(self) -> None:
        """File in models/ → 'critical' with critical threshold."""
        config = self._config()
        tier, threshold = _classify_threshold_tier("trw-mcp/src/trw_mcp/models/config.py", config)
        assert tier == "critical"
        assert threshold == 0.70

    def test_returns_experimental_for_scratch_path(self) -> None:
        """File in scratch/ → 'experimental' with experimental threshold."""
        config = self._config()
        tier, threshold = _classify_threshold_tier("scratch/trw-tester/prototype.py", config)
        assert tier == "experimental"
        assert threshold == 0.30

    def test_returns_standard_for_unmatched_file(self) -> None:
        """File not matching any tier path → 'standard' with standard threshold."""
        config = self._config()
        tier, threshold = _classify_threshold_tier("trw-mcp/tests/test_build.py", config)
        assert tier == "standard"
        assert threshold == 0.50

    def test_critical_takes_precedence_over_experimental(self) -> None:
        """Critical path check runs first; file matching both returns 'critical'."""
        config = TRWConfig(
            mutation_critical_paths=("state/",),
            mutation_experimental_paths=("state/",),
            mutation_threshold_critical=0.70,
            mutation_threshold_experimental=0.30,
        )
        tier, threshold = _classify_threshold_tier("trw-mcp/src/trw_mcp/state/analytics.py", config)
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


class TestGetChangedFilesEdgeCases:
    """Additional edge cases for _get_changed_files."""

    @patch(
        "trw_mcp.tools.mutations.subprocess.run",
        side_effect=FileNotFoundError("git: No such file or directory"),
    )
    def test_returns_empty_list_on_file_not_found_error(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """FileNotFoundError (git binary missing) → returns []."""
        result = _get_changed_files(tmp_path, "trw-mcp/src")
        assert result == []

    @patch("trw_mcp.tools.mutations.subprocess.run")
    def test_whitespace_only_output_returns_empty_list(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """Stdout containing only whitespace/newlines → no files extracted."""
        mock_run.return_value = _make_completed_process(returncode=0, stdout="\n  \n\t\n")
        result = _get_changed_files(tmp_path, "trw-mcp/src")
        assert result == []

    @patch("trw_mcp.tools.mutations.subprocess.run")
    def test_no_py_files_in_source_path_returns_empty(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """All changed files are outside source path → returns []."""
        mock_run.return_value = _make_completed_process(
            returncode=0,
            stdout=("platform/src/app/page.tsx\ndocs/README.md\nMakefile\n"),
        )
        result = _get_changed_files(tmp_path, "trw-mcp/src")
        assert result == []

    @patch("trw_mcp.tools.mutations.subprocess.run")
    def test_git_timeout_expired_returns_empty_list(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """subprocess.TimeoutExpired during git diff → returns []."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)
        result = _get_changed_files(tmp_path, "trw-mcp/src")
        assert result == []

    @patch("trw_mcp.tools.mutations.subprocess.run")
    def test_passes_project_root_as_cwd(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """subprocess.run is called with cwd=str(project_root)."""
        mock_run.return_value = _make_completed_process(returncode=0, stdout="")
        _get_changed_files(tmp_path, "trw-mcp/src")
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs.get("cwd") == str(tmp_path)
