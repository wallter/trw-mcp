"""Transport resolution for the MCP server.

Handles three transport paths:
  - Path 1: Explicit --transport flag -- run as that transport directly.
  - Path 2: No flag + config stdio -- run stdio normally.
  - Path 3: No flag + config HTTP -- auto-start shared server + stdio proxy.
"""

from __future__ import annotations

import argparse

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.server._app import mcp
from trw_mcp.server._proxy import ensure_http_server, run_stdio_proxy


def resolve_and_run_transport(
    args: argparse.Namespace,
    config: TRWConfig,
    *,
    debug: bool,
    log: structlog.stdlib.BoundLogger,
) -> None:
    """Resolve transport mode and start the MCP server.

    Handles three transport paths:
      - Path 1: Explicit ``--transport`` flag -- run as that transport directly.
      - Path 2: No flag + config ``stdio`` -- run stdio normally.
      - Path 3: No flag + config HTTP -- auto-start shared server + stdio proxy.

    Args:
        args: Parsed CLI arguments (transport, host, port).
        config: TRW configuration.
        debug: Whether debug mode is active.
        log: Structured logger.
    """
    # -- Transport resolution (PRD-CORE-070-FR03) -------------------------
    if args.transport is not None:
        # Path 1: Direct server mode (e.g., spawned by ensure_http_server)
        transport: str = args.transport
        host: str = args.host or config.mcp_host
        port: int = args.port or config.mcp_port

        log.info(
            "trw_server_initialized",
            tools_registered=True,
            debug_mode=debug,
            transport=transport,
            host=host,
            port=port,
            mode="direct",
        )

        if transport == "stdio":
            mcp.run()
        else:
            # Pass host/port via transport_kwargs -- mcp.settings is deprecated
            # in FastMCP 2.14+ and silently ignored.
            mcp.run(transport=transport, host=host, port=port)  # type: ignore[arg-type]

    elif config.mcp_transport == "stdio":
        # Path 2: Default stdio -- unchanged behavior
        log.info(
            "trw_server_initialized",
            tools_registered=True,
            debug_mode=debug,
            transport="stdio",
            mode="standalone",
        )
        mcp.run()

    else:
        # Path 3: Auto-start shared HTTP server + run as stdio proxy
        _run_http_proxy_transport(config, log, debug=debug)


def _run_http_proxy_transport(
    config: TRWConfig,
    log: structlog.stdlib.BoundLogger,
    *,
    debug: bool,
) -> None:
    """Start a shared HTTP server and bridge via stdio proxy.

    Falls back to standalone stdio if the HTTP server cannot be started
    or the proxy fails to connect.

    Args:
        config: TRW configuration.
        log: Structured logger.
        debug: Whether debug mode is active.
    """
    log.info(
        "trw_proxy_starting",
        target_transport=config.mcp_transport,
        target_host=config.mcp_host,
        target_port=config.mcp_port,
    )

    url = ensure_http_server(config, log, debug=debug)

    if url is not None:
        # Run stdio proxy bridging to the shared server
        import asyncio

        try:
            asyncio.run(run_stdio_proxy(url))
        except (KeyboardInterrupt, EOFError):
            pass  # Clean exit when Claude Code disconnects
        except ConnectionError:
            # Proxy exhausted retries -- fall back to standalone
            log.warning(
                "trw_proxy_fallback",
                reason="proxy_connect_failed",
                fallback="standalone_stdio",
            )
            mcp.run()
    else:
        # FR06: Fallback to standalone stdio on failure
        log.warning(
            "trw_proxy_fallback",
            reason="http_server_start_failed",
            fallback="standalone_stdio",
        )
        mcp.run()
