"""HTTP server process management and stdio-to-HTTP proxy.

Provides functions for spawning the HTTP MCP server as a background
process and bridging stdio transport to it via an async proxy.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig


def _is_port_open(host: str, port: int) -> bool:
    """Check if a TCP port is accepting connections."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex((host, port)) == 0


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    try:
        os.kill(pid, 0)  # Signal 0: check existence without sending a signal
        return True
    except (OSError, ProcessLookupError):
        return False


def _clean_stale_pid(
    pid_path: Path,
    log: structlog.stdlib.BoundLogger,
) -> None:
    """Remove PID file if the recorded process is no longer alive.

    Args:
        pid_path: Path to the PID file.
        log: Structured logger for audit trail.
    """
    if not pid_path.exists():
        return
    try:
        old_pid = int(pid_path.read_text(encoding="utf-8").strip())
        if not _is_pid_alive(old_pid):
            log.info("mcp_server_stale_pid_cleaned", stale_pid=old_pid)
            pid_path.unlink(missing_ok=True)
    except (ValueError, OSError):
        pid_path.unlink(missing_ok=True)


def _spawn_http_server(
    config: TRWConfig,
    trw_dir: Path,
    *,
    debug: bool,
) -> int:
    """Spawn the HTTP MCP server as a background process.

    Args:
        config: TRW configuration (transport, host, port, logs_dir).
        trw_dir: Resolved ``.trw`` directory path.
        debug: Whether to pass ``--debug`` to the spawned server.

    Returns:
        PID of the spawned server process.
    """
    import subprocess

    cmd = [
        sys.executable,
        "-m",
        "trw_mcp.server",
        "--transport",
        config.mcp_transport,
        "--host",
        config.mcp_host,
        "--port",
        str(config.mcp_port),
    ]
    if debug:
        cmd.append("--debug")

    logs_dir = trw_dir / config.logs_dir
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "mcp-server.log"

    with open(log_file, "a") as log_out:
        proc = subprocess.Popen(  # noqa: S603 — shell=False (default); cmd uses sys.executable (fully-qualified) with validated config args
            cmd,
            stdout=log_out,
            stderr=log_out,
            start_new_session=True,
        )

    pid_path = trw_dir / "mcp-server.pid"
    pid_path.write_text(str(proc.pid), encoding="utf-8")

    return proc.pid


def _wait_for_port(
    host: str,
    port: int,
    *,
    poll_interval: float = 0.5,
    max_polls: int = 60,
) -> bool:
    """Poll until a TCP port accepts connections or timeout is reached.

    Args:
        host: Host address to poll.
        port: Port number to check.
        poll_interval: Seconds between polls (default 0.5s).
        max_polls: Maximum number of polls (default 60 = 30s total).

    Returns:
        True if the port became available, False on timeout.
    """
    import time

    for _ in range(max_polls):
        time.sleep(poll_interval)
        if _is_port_open(host, port):
            return True
    return False


def ensure_http_server(
    config: TRWConfig,
    log: structlog.stdlib.BoundLogger,
    *,
    debug: bool = False,
) -> str | None:
    """Ensure the shared HTTP MCP server is running.

    Auto-starts the server daemon if not already running, using file lock
    to prevent race conditions between concurrent Claude Code instances.
    Cleans up stale PID files from dead processes before attempting start.

    Returns the server URL on success, None on failure (caller should
    fall back to standalone stdio).
    """
    import fcntl

    host = config.mcp_host
    port = config.mcp_port
    transport = config.mcp_transport
    path = "/sse" if transport == "sse" else "/mcp"
    url = f"http://{host}:{port}{path}"

    # Already running -- fast path
    if _is_port_open(host, port):
        log.info("mcp_server_already_running", host=host, port=port)
        return url

    trw_dir = Path.cwd() / config.trw_dir
    trw_dir.mkdir(parents=True, exist_ok=True)
    lock_path = trw_dir / "mcp-server.lock"
    pid_path = trw_dir / "mcp-server.pid"

    _clean_stale_pid(pid_path, log)

    with open(lock_path, "w") as lock_fd:
        try:
            # Non-blocking lock attempt
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # Another process holds the lock -- wait, then re-check port
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
            if _is_port_open(host, port):
                return url

        try:
            pid = _spawn_http_server(config, trw_dir, debug=debug)

            # WSL2 cold starts can take 15-20s due to filesystem I/O latency
            if _wait_for_port(host, port):
                log.info("mcp_server_started", pid=pid, url=url)
                return url

            log.warning("mcp_server_start_timeout", host=host, port=port, timeout_secs=30)
            return None
        except Exception:  # justified: boundary, subprocess spawn can fail in many ways
            log.warning("mcp_server_start_failed", exc_info=True)
            return None
        finally:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)


async def run_stdio_proxy(url: str, max_retries: int = 3) -> None:
    """Bridge stdio transport to a shared HTTP MCP server.

    Creates a lightweight proxy that forwards all MCP operations (tools,
    resources, prompts) from Claude Code (via stdio) to the shared HTTP
    server. Uses MCP SDK primitives -- no external dependencies.

    Retries initial connection up to ``max_retries`` times with exponential
    backoff to handle race conditions where the server is still starting.
    """
    import asyncio as _asyncio

    from mcp import types
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from pydantic import AnyUrl

    log = structlog.get_logger(__name__)

    # Retry loop for initial connection (server may still be starting)
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            async with streamable_http_client(url) as (read, write, _), ClientSession(read, write) as session:
                await session.initialize()

                # Discover remote capabilities once at startup
                tools_result = await session.list_tools()
                resources_result = await session.list_resources()
                prompts_result = await session.list_prompts()

                log.info(
                    "stdio_proxy_connected",
                    url=url,
                    tools=len(tools_result.tools),
                    attempt=attempt + 1,
                )

                proxy = Server("trw-proxy")

                @proxy.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
                async def handle_list_tools(
                    _tr: object = tools_result,
                ) -> list[types.Tool]:
                    return _tr.tools  # type: ignore[attr-defined, no-any-return]

                @proxy.call_tool(validate_input=False)  # type: ignore[untyped-decorator]
                async def handle_call_tool(
                    name: str,
                    arguments: dict[str, object] | None = None,
                ) -> types.CallToolResult:
                    return await session.call_tool(name, arguments)

                @proxy.list_resources()  # type: ignore[no-untyped-call, untyped-decorator]
                async def handle_list_resources(
                    _rr: object = resources_result,
                ) -> list[types.Resource]:
                    return _rr.resources  # type: ignore[attr-defined, no-any-return]

                @proxy.read_resource()  # type: ignore[no-untyped-call, untyped-decorator]
                async def handle_read_resource(uri: AnyUrl) -> str:
                    result = await session.read_resource(uri)
                    if result.contents:
                        c = result.contents[0]
                        text = getattr(c, "text", None)
                        if text is not None:
                            return str(text)
                        blob = getattr(c, "blob", None)
                        if blob is not None:
                            return str(blob)
                    return ""

                @proxy.list_prompts()  # type: ignore[no-untyped-call, untyped-decorator]
                async def handle_list_prompts(
                    _pr: object = prompts_result,
                ) -> list[types.Prompt]:
                    return _pr.prompts  # type: ignore[attr-defined, no-any-return]

                @proxy.get_prompt()  # type: ignore[no-untyped-call, untyped-decorator]
                async def handle_get_prompt(
                    name: str,
                    arguments: dict[str, str] | None = None,
                ) -> types.GetPromptResult:
                    return await session.get_prompt(name, arguments)

                # Run the proxy on stdio -- Claude Code communicates here
                async with stdio_server() as (stdio_read, stdio_write):
                    await proxy.run(
                        stdio_read,
                        stdio_write,
                        proxy.create_initialization_options(),
                    )
                return  # Clean exit
        except (ConnectionError, OSError, Exception) as exc:  # per-item error handling: retry logic per connection attempt  # noqa: PERF203
            last_error = exc
            if attempt < max_retries - 1:
                delay = 2**attempt  # 1s, 2s, 4s
                log.warning(
                    "stdio_proxy_connect_retry",
                    url=url,
                    attempt=attempt + 1,
                    delay_secs=delay,
                    error=str(exc),
                )
                await _asyncio.sleep(delay)

    # All retries exhausted
    log.error(
        "stdio_proxy_connect_failed",
        url=url,
        attempts=max_retries,
        last_error=str(last_error),
    )
    raise ConnectionError(f"Failed to connect to MCP server at {url} after {max_retries} attempts") from last_error
