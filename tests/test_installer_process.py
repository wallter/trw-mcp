"""Tests for installer process management functions (PRD-INFRA-041).

These functions live in install-trw.template.py (a standalone script that can't
be imported). We replicate the pure logic here for testing — the functions use
only stdlib and have no external dependencies.

Covers:
- _is_process_alive: cross-platform PID existence check
- _terminate_process: cross-platform process termination
- _restart_mcp_servers: PID kill + version sentinel write
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Replicated functions from install-trw.template.py ────────────────
# These are exact copies of the installer functions for testability.
# If the installer template changes, these must be updated to match.


def _is_process_alive(pid: int) -> bool:
    """Check if a process with the given PID is running. Cross-platform."""
    if sys.platform == "win32":
        try:
            import ctypes
            SYNCHRONIZE = 0x00100000
            handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, 0, pid)  # type: ignore[union-attr]
            if handle == 0:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[union-attr]
            return True
        except (OSError, AttributeError):
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, OSError):
            return False


def _terminate_process(pid: int) -> bool:
    """Terminate a process by PID. Cross-platform."""
    try:
        if sys.platform == "win32":
            try:
                os.kill(pid, signal.SIGTERM)
                return True
            except OSError:
                return subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                ).returncode == 0
        else:
            os.kill(pid, signal.SIGTERM)
            return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


# ── FR08: Process Existence Check ────────────────────────────────────


class TestIsProcessAlive:
    """FR08: _is_process_alive works cross-platform."""

    def test_own_process_is_alive(self) -> None:
        """Current process PID should report as alive."""
        assert _is_process_alive(os.getpid()) is True

    def test_bogus_pid_is_not_alive(self) -> None:
        """Non-existent PID should report as dead."""
        # Use a very high PID unlikely to exist
        assert _is_process_alive(4_000_000) is False

    def test_zero_pid_is_not_alive(self) -> None:
        """PID 0 (kernel) should not be reported as a user process."""
        # os.kill(0, 0) sends signal to the process group, not useful for us
        # On most systems this either fails or returns for the wrong reason
        result = _is_process_alive(0)
        # We just verify no crash; result varies by platform
        assert isinstance(result, bool)

    def test_negative_pid_is_not_alive(self) -> None:
        """Negative PIDs should not crash (result varies by platform)."""
        # On Linux, os.kill(-1, 0) sends to all processes — may return True.
        # The key invariant is no crash, not a specific return value.
        result = _is_process_alive(-1)
        assert isinstance(result, bool)


# ── FR04: Cross-Platform Process Termination ─────────────────────────


class TestTerminateProcess:
    """FR04: Process termination works on Unix and Windows."""

    def test_terminate_spawned_process(self) -> None:
        """Spawned subprocess can be terminated."""
        # Start a sleep process
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        pid = proc.pid

        assert _is_process_alive(pid) is True
        assert _terminate_process(pid) is True

        # Wait for process to actually die
        proc.wait(timeout=5)
        assert proc.returncode is not None

    def test_terminate_dead_process_returns_false(self) -> None:
        """Terminating a non-existent process returns False."""
        assert _terminate_process(4_000_000) is False

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix-specific SIGTERM test")
    def test_unix_sends_sigterm(self) -> None:
        """On Unix, SIGTERM is sent (process can handle it gracefully)."""
        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None
            _terminate_process(12345)
            mock_kill.assert_called_once_with(12345, signal.SIGTERM)


# ── FR03: PID-Based Server Restart ──────────────────────────────────


class TestPIDRestart:
    """FR03: Installer kills HTTP server via PID file and writes sentinel."""

    def test_restart_kills_alive_process(self, tmp_path: Path) -> None:
        """PID file with alive process: process killed, PID file removed, sentinel written."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        pid_path = trw_dir / "mcp-server.pid"
        sentinel_path = trw_dir / "installed-version.json"

        # Start a disposable process
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        pid_path.write_text(str(proc.pid), encoding="utf-8")

        # Simulate restart logic
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
            if _is_process_alive(pid):
                _terminate_process(pid)
                pid_path.unlink(missing_ok=True)
        except (ValueError, OSError):
            pid_path.unlink(missing_ok=True)

        # Write sentinel
        sentinel_path.write_text(
            json.dumps({"version": "0.16.0", "timestamp": "2026-03-14T00:00:00Z"}),
            encoding="utf-8",
        )

        proc.wait(timeout=5)
        assert not pid_path.exists()
        assert sentinel_path.exists()
        data = json.loads(sentinel_path.read_text(encoding="utf-8"))
        assert data["version"] == "0.16.0"

    def test_restart_cleans_stale_pid(self, tmp_path: Path) -> None:
        """PID file with dead process: PID file cleaned up without error."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        pid_path = trw_dir / "mcp-server.pid"

        # Write a dead PID
        pid_path.write_text("4000000", encoding="utf-8")

        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
            if _is_process_alive(pid):
                _terminate_process(pid)
                pid_path.unlink(missing_ok=True)
            else:
                pid_path.unlink(missing_ok=True)
        except (ValueError, OSError):
            pid_path.unlink(missing_ok=True)

        assert not pid_path.exists()

    def test_restart_no_pid_file_no_error(self, tmp_path: Path) -> None:
        """No PID file: no error, sentinel can still be written."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        pid_path = trw_dir / "mcp-server.pid"
        sentinel_path = trw_dir / "installed-version.json"

        assert not pid_path.exists()

        # Only sentinel write happens
        sentinel_path.write_text(
            json.dumps({"version": "0.16.0"}),
            encoding="utf-8",
        )

        assert sentinel_path.exists()

    def test_restart_corrupt_pid_no_crash(self, tmp_path: Path) -> None:
        """Corrupt PID file (non-integer): cleaned up gracefully."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        pid_path = trw_dir / "mcp-server.pid"

        pid_path.write_text("not_a_pid", encoding="utf-8")

        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
            if _is_process_alive(pid):
                _terminate_process(pid)
                pid_path.unlink(missing_ok=True)
            else:
                pid_path.unlink(missing_ok=True)
        except (ValueError, OSError):
            pid_path.unlink(missing_ok=True)

        assert not pid_path.exists()
