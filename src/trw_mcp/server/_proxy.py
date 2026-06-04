"""HTTP server process management and stdio-to-HTTP proxy.

Provides functions for spawning the HTTP MCP server as a background
process and bridging stdio transport to it via an async proxy.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, NamedTuple

import structlog

from trw_mcp.models.config import TRWConfig


class ProxyCapabilities(NamedTuple):
    """Remote capability payloads discovered before stdio proxy serving starts."""

    tools_result: Any
    resources_result: Any
    prompts_result: Any


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
    max_polls: int = 240,
) -> bool:
    """Poll until a TCP port accepts connections or timeout is reached.

    Args:
        host: Host address to poll.
        port: Port number to check.
        poll_interval: Seconds between polls (default 0.5s).
        max_polls: Maximum number of polls (default 240 = 120s total).

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
    from trw_mcp._locking import _lock_ex, _lock_ex_nb, _lock_un

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
            _lock_ex_nb(lock_fd.fileno())
        except OSError:
            # Another process holds the lock -- wait, then re-check port
            _lock_ex(lock_fd.fileno())
            if _is_port_open(host, port):
                return url

        try:
            pid = _spawn_http_server(config, trw_dir, debug=debug)

            # Large workspaces can spend 30s+ in boot-time stale-run cleanup
            # before Uvicorn binds. Keep the stdio proxy alive long enough for
            # the shared server to come up instead of falling back to a second
            # standalone stdio server.
            startup_wait_secs = max(1, int(config.mcp_startup_wait_seconds))
            poll_interval = 0.5
            max_polls = max(1, int(startup_wait_secs / poll_interval))
            if _wait_for_port(host, port, poll_interval=poll_interval, max_polls=max_polls):
                log.info("mcp_server_started", pid=pid, url=url)
                return url

            log.warning("mcp_server_start_timeout", host=host, port=port, timeout_secs=startup_wait_secs)
            return None
        except Exception:  # justified: boundary, subprocess spawn can fail in many ways
            log.warning("mcp_server_start_failed", exc_info=True)
            return None
        finally:
            _lock_un(lock_fd.fileno())


async def discover_proxy_capabilities(
    session: Any,
    *,
    timeout_seconds: float,
) -> ProxyCapabilities:
    """Discover remote MCP capabilities within one total handshake budget.

    The foreground stdio process must not stay silent longer than common MCP
    client reconnect windows.  Apply one budget across remote initialize,
    tools, resources, and prompts discovery so a slow shared HTTP server causes
    a bounded retry/fallback instead of a client-visible 30s reconnect timeout.
    """

    async def _discover() -> ProxyCapabilities:
        await session.initialize()
        tools_result = await session.list_tools()
        resources_result = await session.list_resources()
        prompts_result = await session.list_prompts()
        return ProxyCapabilities(
            tools_result=tools_result,
            resources_result=resources_result,
            prompts_result=prompts_result,
        )

    try:
        return await asyncio.wait_for(_discover(), timeout=timeout_seconds)
    except TimeoutError as exc:
        raise TimeoutError(f"proxy capability discovery timed out after {timeout_seconds:.3f}s") from exc


async def run_stdio_proxy(
    url: str,
    max_retries: int = 3,
    *,
    handshake_timeout_seconds: float = 8.0,
    request_timeout_seconds: float = 60.0,
) -> None:
    """Bridge stdio transport to a shared HTTP MCP server.

    Creates a lightweight proxy that forwards all MCP operations (tools,
    resources, prompts) from Claude Code (via stdio) to the shared HTTP
    server. Uses MCP SDK primitives -- no external dependencies.

    Retries initial connection up to ``max_retries`` times with exponential
    backoff to handle race conditions where the server is still starting.

    ``request_timeout_seconds`` bounds every forwarded MCP request at the
    session level (PRD-FIX-106). Without it the MCP SDK waits with
    ``anyio.fail_after(None)`` -- an UNBOUNDED block -- so a restarted/replaced
    shared HTTP server (new PID) whose in-flight response is orphaned hangs the
    agent indefinitely instead of surfacing a REQUEST_TIMEOUT ``McpError``.
    """
    import time
    from datetime import timedelta

    from mcp import McpError, types
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from pydantic import AnyUrl

    log = structlog.get_logger(__name__)
    # Bound the session-level read wait so a gone/replaced backend fails fast
    # rather than blocking on anyio.fail_after(None). (PRD-FIX-106)
    request_timeout = timedelta(seconds=request_timeout_seconds)
    # The MCP SDK raises McpError with this code (httpx REQUEST_TIMEOUT) when the
    # bounded session read timeout elapses (mcp/shared/session.py send_request).
    REQUEST_TIMEOUT = 408

    # Retry loop for initial connection (server may still be starting)
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            async with (
                streamable_http_client(url) as (read, write, _),
                ClientSession(read, write, read_timeout_seconds=request_timeout) as session,
            ):
                # Discover remote capabilities once at startup. This is on the
                # client reconnect critical path, so bound the total remote
                # discovery time and let the existing retry/fallback path handle
                # transient shared-server pressure.
                discovery_start = time.monotonic()
                capabilities = await discover_proxy_capabilities(
                    session,
                    timeout_seconds=handshake_timeout_seconds,
                )
                discovery_elapsed_ms = round((time.monotonic() - discovery_start) * 1000, 2)
                tools_result = capabilities.tools_result
                resources_result = capabilities.resources_result
                prompts_result = capabilities.prompts_result

                log.info(
                    "stdio_proxy_connected",
                    url=url,
                    tools=len(tools_result.tools),
                    resources=len(resources_result.resources),
                    prompts=len(prompts_result.prompts),
                    attempt=attempt + 1,
                    discovery_elapsed_ms=discovery_elapsed_ms,
                    handshake_timeout_seconds=handshake_timeout_seconds,
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
                    # Bound the forwarded call: a gone/replaced backend raises a
                    # REQUEST_TIMEOUT McpError within request_timeout instead of
                    # blocking forever. (PRD-FIX-106). The error is surfaced to the
                    # agent -- never swallowed or retried into another unbounded wait.
                    try:
                        return await session.call_tool(
                            name,
                            arguments,
                            read_timeout_seconds=request_timeout,
                        )
                    except McpError as exc:
                        if getattr(getattr(exc, "error", None), "code", None) == REQUEST_TIMEOUT:
                            log.warning(
                                "stdio_proxy_backend_timeout",
                                url=url,
                                tool=name,
                                request_timeout_seconds=request_timeout_seconds,
                                detail=(
                                    "Forwarded tool call timed out -- shared HTTP backend is "
                                    "gone/replaced. Proxy will surface the error so the host "
                                    "client can re-spawn and rebind."
                                ),
                            )
                        raise

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
        except (
            ConnectionError,
            OSError,
            Exception,
        ) as exc:  # per-item error handling: retry logic per connection attempt
            last_error = exc
            if attempt < max_retries - 1:
                delay = 2**attempt  # 1s, 2s, 4s
                log.warning(
                    "stdio_proxy_connect_retry",
                    url=url,
                    attempt=attempt + 1,
                    delay_secs=delay,
                    handshake_timeout_seconds=handshake_timeout_seconds,
                    error=str(exc),
                )
                await asyncio.sleep(delay)

    # All retries exhausted
    log.error(
        "stdio_proxy_connect_failed",
        url=url,
        attempts=max_retries,
        last_error=str(last_error),
    )
    raise ConnectionError(f"Failed to connect to MCP server at {url} after {max_retries} attempts") from last_error
