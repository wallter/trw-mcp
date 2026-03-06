"""Shared HTTP MCP server auto-start and stdio proxy (PRD-CORE-070).

Provides functions for:
- Checking if the HTTP server is running (port probe)
- Auto-starting the HTTP server as a detached daemon
- Running a stdio-to-HTTP proxy for Claude Code
- Process management (PID file, file lock, cleanup)
"""

from __future__ import annotations

import fcntl
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import structlog

logger = structlog.get_logger()


def is_port_open(host: str, port: int) -> bool:
    """Check if a TCP port is accepting connections.

    Args:
        host: Hostname or IP to check.
        port: TCP port number.

    Returns:
        True if the port accepts a connection, False otherwise.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            result = sock.connect_ex((host, port))
            return result == 0
    except OSError:
        return False


def _pid_file_path(trw_dir: Path) -> Path:
    return trw_dir / "mcp-server.pid"


def _lock_file_path(trw_dir: Path) -> Path:
    return trw_dir / "mcp-server.lock"


def read_server_pid(trw_dir: Path) -> int | None:
    """Read the server PID from the PID file.

    Returns None if file doesn't exist or PID is invalid/stale.
    """
    pid_path = _pid_file_path(trw_dir)
    if not pid_path.exists():
        return None

    try:
        pid = int(pid_path.read_text().strip())
        # Check if process is alive
        os.kill(pid, 0)
        return pid
    except (ValueError, OSError):
        # Stale PID file — clean up
        try:
            pid_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def _write_pid(trw_dir: Path, pid: int) -> None:
    """Write PID to file."""
    pid_path = _pid_file_path(trw_dir)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(pid))


def ensure_http_server(
    trw_dir: Path,
    host: str = "127.0.0.1",
    port: int = 8100,
    timeout: float = 15.0,
    debug: bool = False,
) -> bool:
    """Ensure the shared HTTP server is running.

    If the server is not running, acquires a file lock and spawns it
    as a detached subprocess. Waits for the server to start accepting
    connections.

    Args:
        trw_dir: Path to .trw directory for PID/lock files.
        host: Host to bind/check.
        port: Port to bind/check.
        timeout: Max seconds to wait for server startup.
        debug: Pass --debug flag to server.

    Returns:
        True if server is running (or was started), False on failure.
    """
    # Already running?
    if is_port_open(host, port):
        logger.debug("http_server_already_running", host=host, port=port)
        return True

    # Acquire file lock to prevent race conditions
    lock_path = _lock_file_path(trw_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        lock_fd = open(str(lock_path), "w")  # noqa: SIM115
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError):
        # Another process is starting the server — wait for it
        logger.debug("http_server_lock_held_waiting", host=host, port=port)
        return _wait_for_port(host, port, timeout)

    try:
        # Double-check after lock acquisition
        if is_port_open(host, port):
            return True

        # Spawn server as detached subprocess
        cmd = [
            sys.executable, "-m", "trw_mcp",
            "--transport", "streamable-http",
            "--host", host,
            "--port", str(port),
        ]
        if debug:
            cmd.append("--debug")

        logger.info("starting_http_server", cmd=cmd)

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        _write_pid(trw_dir, proc.pid)

        # Wait for port to open
        if _wait_for_port(host, port, timeout):
            logger.info("http_server_started", pid=proc.pid, host=host, port=port)
            return True
        else:
            logger.warning("http_server_startup_timeout", pid=proc.pid, timeout=timeout)
            return False

    finally:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            lock_fd.close()
        except OSError:
            pass


def _wait_for_port(host: str, port: int, timeout: float) -> bool:
    """Poll a port until it's open or timeout expires."""
    deadline = time.monotonic() + timeout
    interval = 0.5
    while time.monotonic() < deadline:
        if is_port_open(host, port):
            return True
        time.sleep(interval)
    return False


def stop_server(trw_dir: Path) -> bool:
    """Stop the shared HTTP server by PID.

    Returns True if a server was stopped, False if none was running.
    """
    pid = read_server_pid(trw_dir)
    if pid is None:
        return False

    try:
        os.kill(pid, signal.SIGTERM)
        # Wait briefly for clean shutdown
        for _ in range(10):
            try:
                os.kill(pid, 0)
                time.sleep(0.5)
            except OSError:
                break

        # Clean up PID file
        _pid_file_path(trw_dir).unlink(missing_ok=True)
        logger.info("http_server_stopped", pid=pid)
        return True
    except OSError:
        _pid_file_path(trw_dir).unlink(missing_ok=True)
        return False


def server_status(trw_dir: Path, host: str = "127.0.0.1", port: int = 8100) -> dict[str, object]:
    """Get server status.

    Returns dict with: running (bool), pid (int|None), host, port.
    """
    pid = read_server_pid(trw_dir)
    running = is_port_open(host, port)

    return {
        "running": running,
        "pid": pid,
        "host": host,
        "port": port,
    }
