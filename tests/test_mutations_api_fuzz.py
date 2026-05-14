from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools.build import _API_FUZZ_FILE, _cache_to_context, _run_api_fuzz

from ._mutations_support import _make_completed_process


class TestRunApiFuzz:
    """Tests for _run_api_fuzz."""

    @patch("trw_mcp.tools.build._audit._find_executable", return_value=None)
    def test_skips_when_schemathesis_not_installed(self, mock_find: MagicMock, tmp_path: Path) -> None:
        """Returns api_fuzz_skipped=True when neither schemathesis nor st is installed."""
        config = TRWConfig()
        result = _run_api_fuzz(tmp_path, config)
        assert result.get("api_fuzz_skipped") is True
        assert "schemathesis not installed" in str(result.get("api_fuzz_skip_reason", ""))

    @patch(
        "trw_mcp.tools.build._audit._find_executable",
        return_value="/usr/local/bin/schemathesis",
    )
    @patch("urllib.request.urlopen")
    @patch("trw_mcp.tools.build._audit._run_subprocess")
    def test_passed_true_when_schemathesis_exits_zero(
        self,
        mock_subprocess: MagicMock,
        mock_urlopen: MagicMock,
        mock_find: MagicMock,
        tmp_path: Path,
    ) -> None:
        """api_fuzz_passed=True when schemathesis exits 0."""
        mock_urlopen.return_value = MagicMock()
        mock_subprocess.return_value = _make_completed_process(returncode=0, stdout="All checks passed")
        config = TRWConfig(api_fuzz_base_url="http://localhost:8000")
        result = _run_api_fuzz(tmp_path, config)
        assert result.get("api_fuzz_passed") is True

    @patch(
        "trw_mcp.tools.build._audit._find_executable",
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
        "trw_mcp.tools.build._audit._find_executable",
        return_value="/usr/local/bin/schemathesis",
    )
    @patch("urllib.request.urlopen")
    @patch("trw_mcp.tools.build._audit._run_subprocess")
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
        "trw_mcp.tools.build._audit._find_executable",
        return_value="/usr/local/bin/schemathesis",
    )
    @patch("urllib.request.urlopen")
    @patch(
        "trw_mcp.tools.build._audit._run_subprocess",
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
        "trw_mcp.tools.build._audit._find_executable",
        return_value="/usr/local/bin/schemathesis",
    )
    @patch("urllib.request.urlopen")
    @patch("trw_mcp.tools.build._audit._run_subprocess")
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
            stdout=("Running checks...\nFAILED /api/items - Defect found\nERROR /api/users - 500 Server Error\n"),
        )
        config = TRWConfig()
        result = _run_api_fuzz(tmp_path, config)
        assert result.get("api_fuzz_failure_count", 0) >= 1


class TestCacheApiFuzz:
    """Tests for _cache_to_context with _API_FUZZ_FILE."""

    def test_writes_yaml_to_correct_path(self, tmp_path: Path) -> None:
        """Writes api-fuzz-status.yaml to .trw/context/."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        result = {"api_fuzz_passed": True, "api_fuzz_base_url": "http://localhost:8000"}
        path = _cache_to_context(trw_dir, _API_FUZZ_FILE, result)
        assert path.name == "api-fuzz-status.yaml"
        assert path.parent.name == "context"
        assert path.exists()

    def test_written_data_is_readable(self, tmp_path: Path) -> None:
        """Written YAML can be read back correctly."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        result = {"api_fuzz_passed": False, "api_fuzz_failure_count": 3}
        path = _cache_to_context(trw_dir, _API_FUZZ_FILE, result)
        data = FileStateReader().read_yaml(path)
        assert data["api_fuzz_passed"] is False
        assert data["api_fuzz_failure_count"] == 3
