"""Tests for scoring/rework_rate.py — PRD-CORE-104-FR01.

Covers: compute_rework_rate with git-based rework detection.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest
from structlog.testing import capture_logs

from trw_mcp.scoring.rework_rate import compute_rework_rate


@pytest.fixture()
def _mock_git_log():
    """Helper to create a mock subprocess.run that returns given output."""

    def _make(stdout: str, returncode: int = 0):
        result = MagicMock()
        result.stdout = stdout
        result.returncode = returncode
        return result

    return _make


class TestComputeReworkRate:
    """Tests for compute_rework_rate()."""

    def test_empty_files_returns_0(self) -> None:
        result = compute_rework_rate([])
        assert result == {"rework_rate": 0.0, "rework_files": 0, "total_files": 0}

    @patch("trw_mcp.scoring.rework_rate.subprocess.run")
    def test_basic_rework_detection(self, mock_run: MagicMock) -> None:
        """A file with a 'fix: ...' commit in history is rework."""
        mock_run.return_value = MagicMock(
            stdout="fix: correct typo in module\nfeat: add new feature\n",
            returncode=0,
        )
        result = compute_rework_rate(["src/foo.py"])
        assert result["rework_rate"] > 0
        assert result["rework_files"] == 1
        assert result["total_files"] == 1

    @patch("trw_mcp.scoring.rework_rate.subprocess.run")
    def test_no_rework_returns_0(self, mock_run: MagicMock) -> None:
        """Files with only non-fix commits have 0 rework."""
        mock_run.return_value = MagicMock(
            stdout="feat: add new feature\nrefactor: clean up imports\n",
            returncode=0,
        )
        result = compute_rework_rate(["src/foo.py", "src/bar.py"])
        assert result["rework_rate"] == 0.0
        assert result["rework_files"] == 0
        assert result["total_files"] == 2

    @patch("trw_mcp.scoring.rework_rate.subprocess.run")
    def test_all_rework_returns_1(self, mock_run: MagicMock) -> None:
        """All files having fix commits yields rate 1.0."""
        mock_run.return_value = MagicMock(
            stdout="fix: patch security hole\n",
            returncode=0,
        )
        result = compute_rework_rate(["a.py", "b.py", "c.py"])
        assert result["rework_rate"] == 1.0
        assert result["rework_files"] == 3
        assert result["total_files"] == 3

    @patch("trw_mcp.scoring.rework_rate.subprocess.run")
    def test_git_timeout_returns_0_with_warning(self, mock_run: MagicMock) -> None:
        """TimeoutExpired on git is handled gracefully."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=5)
        result = compute_rework_rate(["src/foo.py"])
        assert result["rework_rate"] == 0.0
        assert result["rework_files"] == 0

    @patch("trw_mcp.scoring.rework_rate.subprocess.run")
    def test_git_unavailable_returns_0(self, mock_run: MagicMock) -> None:
        """OSError (git not found) is handled gracefully."""
        mock_run.side_effect = OSError("git not found")
        result = compute_rework_rate(["src/foo.py"])
        assert result["rework_rate"] == 0.0
        assert result["rework_files"] == 0

    @patch("trw_mcp.scoring.rework_rate.subprocess.run")
    def test_batch_50_files(self, mock_run: MagicMock) -> None:
        """60 files process correctly across 2 batches."""
        mock_run.return_value = MagicMock(
            stdout="feat: something\n",
            returncode=0,
        )
        files = [f"file_{i}.py" for i in range(60)]
        result = compute_rework_rate(files)
        assert result["total_files"] == 60
        assert result["rework_files"] == 0
        assert mock_run.call_count == 60

    @patch("trw_mcp.scoring.rework_rate.subprocess.run")
    def test_fix_prefix_matching(self, mock_run: MagicMock) -> None:
        """fix:, hotfix:, revert: all count as rework."""
        prefixes = ["fix: blah", "hotfix: blah", "revert: blah"]
        files = ["a.py", "b.py", "c.py"]
        mock_run.side_effect = [
            MagicMock(stdout=prefixes[0], returncode=0),
            MagicMock(stdout=prefixes[1], returncode=0),
            MagicMock(stdout=prefixes[2], returncode=0),
        ]
        result = compute_rework_rate(files)
        assert result["rework_files"] == 3
        assert result["rework_rate"] == 1.0

    @patch("trw_mcp.scoring.rework_rate.subprocess.run")
    def test_timeout_structured_log_field(self, mock_run: MagicMock) -> None:
        """Verify structlog field rework_rate_git_timeout=True on timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=5)
        with capture_logs() as cap_logs:
            compute_rework_rate(["src/foo.py"])
        timeout_logs = [log for log in cap_logs if log.get("rework_rate_git_timeout") is True]
        assert len(timeout_logs) >= 1
