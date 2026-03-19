"""Tests for MCP config portability (PRD-FIX-037).

Verifies that .mcp.json always uses portable commands (no absolute paths),
_merge_mcp_json preserves user servers and handles edge cases, and
_check_mcp_json_portability warns on stale absolute paths.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# _trw_mcp_server_entry — portable command generation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTrwMcpServerEntryPortable:
    """Verify _trw_mcp_server_entry returns portable (non-absolute) commands."""

    def test_returns_bare_command_when_on_path(self) -> None:
        """When trw-mcp is found on PATH, return bare 'trw-mcp' command."""
        from trw_mcp.bootstrap._utils import _trw_mcp_server_entry

        with patch("trw_mcp.bootstrap._utils.shutil") as mock_shutil:
            mock_shutil.which.return_value = "/usr/local/bin/trw-mcp"
            entry = _trw_mcp_server_entry()

        assert entry["command"] == "trw-mcp"
        assert entry["args"] == ["--debug"]
        # Must NOT contain any absolute path
        assert not str(entry["command"]).startswith("/")

    def test_returns_sys_executable_fallback_when_not_on_path(self) -> None:
        """When trw-mcp is not on PATH, return sys.executable -m trw_mcp.server."""
        from trw_mcp.bootstrap._utils import _trw_mcp_server_entry

        with patch("trw_mcp.bootstrap._utils.shutil") as mock_shutil:
            mock_shutil.which.return_value = None
            entry = _trw_mcp_server_entry()

        assert entry["command"] == sys.executable
        assert entry["args"] == ["-m", "trw_mcp.server", "--debug"]

    def test_on_path_returns_bare_command_not_absolute(self) -> None:
        """When trw-mcp is on PATH, command is bare name (not the resolved absolute path)."""
        from trw_mcp.bootstrap._utils import _trw_mcp_server_entry

        with patch("trw_mcp.bootstrap._utils.shutil") as mock_shutil:
            mock_shutil.which.return_value = "/some/path/trw-mcp"
            entry = _trw_mcp_server_entry()
        assert entry["command"] == "trw-mcp"
        assert not str(entry["command"]).startswith("/")

    def test_entry_always_has_debug_arg(self) -> None:
        """Both paths include --debug in args."""
        from trw_mcp.bootstrap._utils import _trw_mcp_server_entry

        with patch("trw_mcp.bootstrap._utils.shutil") as mock_shutil:
            mock_shutil.which.return_value = "/usr/bin/trw-mcp"
            entry = _trw_mcp_server_entry()
        assert "--debug" in entry["args"]  # type: ignore[operator]

        with patch("trw_mcp.bootstrap._utils.shutil") as mock_shutil:
            mock_shutil.which.return_value = None
            entry = _trw_mcp_server_entry()
        assert "--debug" in entry["args"]  # type: ignore[operator]


# ---------------------------------------------------------------------------
# _merge_mcp_json — preservation, idempotency, malformed handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMergeMcpJsonPortable:
    """Verify _merge_mcp_json preserves user servers and handles edge cases."""

    def _make_result(self) -> dict[str, list[str]]:
        return {
            "created": [],
            "updated": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
        }

    def test_preserves_non_trw_servers(self, tmp_path: Path) -> None:
        """Non-trw MCP server entries are preserved after merge."""
        from trw_mcp.bootstrap._utils import _merge_mcp_json

        existing = {
            "mcpServers": {
                "other-tool": {"command": "other-cmd", "args": []},
                "trw": {"command": "/old/absolute/trw-mcp", "args": ["--debug"]},
            }
        }
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text(json.dumps(existing), encoding="utf-8")

        result = self._make_result()
        _merge_mcp_json(tmp_path, result)

        data = json.loads(mcp_path.read_text(encoding="utf-8"))
        # Other server must be preserved
        assert "other-tool" in data["mcpServers"]
        assert data["mcpServers"]["other-tool"]["command"] == "other-cmd"
        # trw entry must be updated
        assert "trw" in data["mcpServers"]

    def test_idempotent_run(self, tmp_path: Path) -> None:
        """Running _merge_mcp_json twice produces identical output."""
        from trw_mcp.bootstrap._utils import _merge_mcp_json

        result1 = self._make_result()
        _merge_mcp_json(tmp_path, result1)
        content1 = (tmp_path / ".mcp.json").read_text(encoding="utf-8")

        result2 = self._make_result()
        _merge_mcp_json(tmp_path, result2)
        content2 = (tmp_path / ".mcp.json").read_text(encoding="utf-8")

        assert content1 == content2

    def test_handles_malformed_json(self, tmp_path: Path) -> None:
        """Malformed .mcp.json is overwritten with correct content."""
        from trw_mcp.bootstrap._utils import _merge_mcp_json

        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text("{not valid json", encoding="utf-8")

        result = self._make_result()
        _merge_mcp_json(tmp_path, result)

        data = json.loads(mcp_path.read_text(encoding="utf-8"))
        assert "trw" in data["mcpServers"]

    def test_creates_from_scratch(self, tmp_path: Path) -> None:
        """When .mcp.json doesn't exist, creates it with trw entry."""
        from trw_mcp.bootstrap._utils import _merge_mcp_json

        result = self._make_result()
        _merge_mcp_json(tmp_path, result)

        mcp_path = tmp_path / ".mcp.json"
        assert mcp_path.exists()
        data = json.loads(mcp_path.read_text(encoding="utf-8"))
        assert "trw" in data["mcpServers"]
        assert len(result["created"]) > 0

    def test_merged_entry_is_portable(self, tmp_path: Path) -> None:
        """The trw entry written by _merge_mcp_json must be portable."""
        from trw_mcp.bootstrap._utils import _merge_mcp_json

        result = self._make_result()
        _merge_mcp_json(tmp_path, result)

        data = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
        cmd = str(data["mcpServers"]["trw"]["command"])
        assert not cmd.startswith("/"), f"Command must be portable, got: {cmd}"


# ---------------------------------------------------------------------------
# _check_mcp_json_portability — server startup diagnostic
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckMcpJsonPortability:
    """Verify _check_mcp_json_portability warns on absolute paths."""

    def test_warns_on_absolute_path_that_does_not_exist(self, tmp_path: Path) -> None:
        """Logs warning when command is an absolute path that doesn't exist."""
        from trw_mcp.server import _check_mcp_json_portability

        mcp_data = {
            "mcpServers": {
                "trw": {
                    "command": "/nonexistent/path/trw-mcp",
                    "args": ["--debug"],
                }
            }
        }
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text(json.dumps(mcp_data), encoding="utf-8")

        with patch("trw_mcp.server._cli.structlog") as mock_structlog:
            mock_logger = mock_structlog.get_logger.return_value
            _check_mcp_json_portability(tmp_path)
            mock_logger.warning.assert_called_once()
            call_kwargs = mock_logger.warning.call_args
            assert "stale_mcp_json_path" in str(call_kwargs)

    def test_silent_on_portable_command(self, tmp_path: Path) -> None:
        """No warning when command is a portable bare name."""
        from trw_mcp.server import _check_mcp_json_portability

        mcp_data = {
            "mcpServers": {
                "trw": {
                    "command": "trw-mcp",
                    "args": ["--debug"],
                }
            }
        }
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text(json.dumps(mcp_data), encoding="utf-8")

        with patch("trw_mcp.server._cli.structlog") as mock_structlog:
            mock_logger = mock_structlog.get_logger.return_value
            _check_mcp_json_portability(tmp_path)
            mock_logger.warning.assert_not_called()

    def test_silent_when_no_mcp_json(self, tmp_path: Path) -> None:
        """No warning when .mcp.json doesn't exist."""
        from trw_mcp.server import _check_mcp_json_portability

        with patch("trw_mcp.server._cli.structlog") as mock_structlog:
            mock_logger = mock_structlog.get_logger.return_value
            _check_mcp_json_portability(tmp_path)
            mock_logger.warning.assert_not_called()

    def test_silent_on_absolute_path_that_exists(self, tmp_path: Path) -> None:
        """No warning when command is an absolute path that exists."""
        from trw_mcp.server import _check_mcp_json_portability

        # Create a fake executable
        fake_cmd = tmp_path / "trw-mcp"
        fake_cmd.write_text("#!/bin/sh\n", encoding="utf-8")

        mcp_data = {
            "mcpServers": {
                "trw": {
                    "command": str(fake_cmd),
                    "args": ["--debug"],
                }
            }
        }
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text(json.dumps(mcp_data), encoding="utf-8")

        with patch("trw_mcp.server._cli.structlog") as mock_structlog:
            mock_logger = mock_structlog.get_logger.return_value
            _check_mcp_json_portability(tmp_path)
            mock_logger.warning.assert_not_called()

    def test_handles_malformed_json_gracefully(self, tmp_path: Path) -> None:
        """Malformed .mcp.json does not raise — just returns silently."""
        from trw_mcp.server import _check_mcp_json_portability

        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text("{bad json", encoding="utf-8")

        # Should not raise
        _check_mcp_json_portability(tmp_path)

    def test_handles_missing_trw_entry(self, tmp_path: Path) -> None:
        """No warning when .mcp.json exists but has no trw entry."""
        from trw_mcp.server import _check_mcp_json_portability

        mcp_data = {"mcpServers": {"other": {"command": "other-cmd"}}}
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text(json.dumps(mcp_data), encoding="utf-8")

        with patch("trw_mcp.server._cli.structlog") as mock_structlog:
            mock_logger = mock_structlog.get_logger.return_value
            _check_mcp_json_portability(tmp_path)
            mock_logger.warning.assert_not_called()
