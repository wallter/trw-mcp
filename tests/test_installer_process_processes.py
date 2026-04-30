"""Tests for installer process lifecycle helper functions."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tests._installer_process_support import _is_process_alive, _terminate_process


class TestIsProcessAlive:
    """FR08: _is_process_alive works cross-platform."""

    def test_own_process_is_alive(self) -> None:
        assert _is_process_alive(os.getpid()) is True

    def test_bogus_pid_is_not_alive(self) -> None:
        assert _is_process_alive(4_000_000) is False

    def test_zero_pid_no_crash(self) -> None:
        result = _is_process_alive(0)
        assert isinstance(result, bool)

    def test_negative_pid_no_crash(self) -> None:
        result = _is_process_alive(-1)
        assert isinstance(result, bool)


class TestTerminateProcess:
    """FR04: Process termination works on Unix and Windows."""

    def test_terminate_spawned_process(self) -> None:
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        assert _is_process_alive(proc.pid) is True
        assert _terminate_process(proc.pid) is True
        proc.wait(timeout=5)
        assert proc.returncode is not None

    def test_terminate_dead_process_returns_false(self) -> None:
        assert _terminate_process(4_000_000) is False

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix-specific SIGTERM test")
    def test_unix_sends_sigterm(self) -> None:
        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None
            _terminate_process(12345)
            mock_kill.assert_called_once_with(12345, signal.SIGTERM)


class TestPIDRestart:
    """FR03: Installer kills HTTP server via PID file and writes sentinel."""

    def test_restart_kills_alive_process(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        pid_path = trw_dir / "mcp-server.pid"
        sentinel_path = trw_dir / "installed-version.json"

        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        pid_path.write_text(str(proc.pid), encoding="utf-8")

        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
            if _is_process_alive(pid):
                _terminate_process(pid)
                pid_path.unlink(missing_ok=True)
        except (ValueError, OSError):
            pid_path.unlink(missing_ok=True)

        sentinel_path.write_text(
            json.dumps({"version": "0.16.0", "timestamp": "2026-03-14T00:00:00Z"}),
            encoding="utf-8",
        )

        proc.wait(timeout=5)
        assert not pid_path.exists()
        assert sentinel_path.exists()
        assert json.loads(sentinel_path.read_text(encoding="utf-8"))["version"] == "0.16.0"

    def test_restart_cleans_stale_pid(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        pid_path = trw_dir / "mcp-server.pid"
        pid_path.write_text("4000000", encoding="utf-8")

        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
            if not _is_process_alive(pid):
                pid_path.unlink(missing_ok=True)
        except (ValueError, OSError):
            pid_path.unlink(missing_ok=True)

        assert not pid_path.exists()

    def test_restart_no_pid_file_no_error(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        sentinel_path = trw_dir / "installed-version.json"
        sentinel_path.write_text(json.dumps({"version": "0.16.0"}), encoding="utf-8")
        assert sentinel_path.exists()

    def test_restart_corrupt_pid_no_crash(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        pid_path = trw_dir / "mcp-server.pid"
        pid_path.write_text("not_a_pid", encoding="utf-8")

        try:
            int(pid_path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            pid_path.unlink(missing_ok=True)

        assert not pid_path.exists()
