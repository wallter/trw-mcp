"""Tests for Shared HTTP MCP Server (PRD-CORE-070)."""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.server_transport import (
    _wait_for_port,
    ensure_http_server,
    is_port_open,
    read_server_pid,
    server_status,
    stop_server,
)


class TestIsPortOpen:
    """Port probe tests."""

    def test_closed_port(self):
        # Use a random high port that's almost certainly closed
        assert is_port_open("127.0.0.1", 59999) is False

    def test_open_port(self):
        # Bind a temporary socket to check
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            port = s.getsockname()[1]
            assert is_port_open("127.0.0.1", port) is True


class TestPidFile:
    """Process management tests."""

    def test_no_pid_file(self, tmp_path: Path):
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        assert read_server_pid(trw_dir) is None

    def test_stale_pid_file(self, tmp_path: Path):
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "mcp-server.pid").write_text("999999999")
        assert read_server_pid(trw_dir) is None

    def test_valid_pid_file(self, tmp_path: Path):
        import os
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # Use current PID (always valid)
        (trw_dir / "mcp-server.pid").write_text(str(os.getpid()))
        assert read_server_pid(trw_dir) == os.getpid()


class TestEnsureHttpServer:
    """FR01: Auto-start HTTP server daemon."""

    def test_already_running(self, tmp_path: Path):
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        with patch("trw_mcp.server_transport.is_port_open", return_value=True):
            result = ensure_http_server(trw_dir, timeout=1.0)
        assert result is True

    def test_spawn_server(self, tmp_path: Path):
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        mock_popen = MagicMock()
        mock_popen.pid = 12345

        call_count = [0]
        def fake_port_check(host, port):
            call_count[0] += 1
            return call_count[0] > 2  # Third call succeeds

        with patch("trw_mcp.server_transport.is_port_open", side_effect=fake_port_check):
            with patch("subprocess.Popen", return_value=mock_popen):
                result = ensure_http_server(trw_dir, timeout=5.0)

        assert result is True
        assert (trw_dir / "mcp-server.pid").exists()

    def test_spawn_timeout(self, tmp_path: Path):
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        mock_popen = MagicMock()
        mock_popen.pid = 12345

        with patch("trw_mcp.server_transport.is_port_open", return_value=False):
            with patch("subprocess.Popen", return_value=mock_popen):
                result = ensure_http_server(trw_dir, timeout=1.0)

        assert result is False


class TestStopServer:
    """FR05: Process management."""

    def test_stop_no_server(self, tmp_path: Path):
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        assert stop_server(trw_dir) is False

    def test_stop_stale_pid(self, tmp_path: Path):
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "mcp-server.pid").write_text("999999999")
        assert stop_server(trw_dir) is False


class TestServerStatus:
    """Server status reporting."""

    def test_status_not_running(self, tmp_path: Path):
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        with patch("trw_mcp.server_transport.is_port_open", return_value=False):
            result = server_status(trw_dir)
        assert result["running"] is False
        assert result["pid"] is None

    def test_status_running(self, tmp_path: Path):
        import os
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "mcp-server.pid").write_text(str(os.getpid()))
        with patch("trw_mcp.server_transport.is_port_open", return_value=True):
            result = server_status(trw_dir)
        assert result["running"] is True
        assert result["pid"] == os.getpid()


class TestTransportResolution:
    """FR03: Transport resolution logic.

    These verify the helper functions. Full transport resolution
    integration is tested when server.py is updated.
    """

    def test_wait_for_port_immediate(self):
        with patch("trw_mcp.server_transport.is_port_open", return_value=True):
            assert _wait_for_port("127.0.0.1", 8100, 1.0) is True

    def test_wait_for_port_timeout(self):
        with patch("trw_mcp.server_transport.is_port_open", return_value=False):
            assert _wait_for_port("127.0.0.1", 8100, 0.5) is False
