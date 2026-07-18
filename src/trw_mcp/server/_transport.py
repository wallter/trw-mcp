"""Transport startup for the MCP server.

trw-mcp is stdio-only: every MCP client spawns its own server instance and
communicates over stdio. This is the portability boundary — no HTTP transport,
no shared server, no proxy.
"""

from __future__ import annotations

import structlog

from trw_mcp.server._app import mcp


def resolve_and_run_transport(
    *,
    debug: bool,
    log: structlog.stdlib.BoundLogger,
) -> None:
    """Start the MCP server on stdio.

    Args:
        debug: Whether debug mode is active.
        log: Structured logger.
    """
    log.info(
        "trw_server_initialized",
        tools_registered=True,
        debug_mode=debug,
        transport="stdio",
        mode="standalone",
    )
    mcp.run()
