"""Transport startup for the MCP server.

The default transport is stdio: every MCP client spawns its own server
instance and communicates over stdio. The opt-in shared mode
(``trw_mcp.shared_server``, off unless ``shared_mcp.enabled``) never passes
through this module.
"""

from __future__ import annotations

import structlog

from trw_mcp.server._app import mcp
from trw_mcp.server._boot_timeline import emit_boot_phase
from trw_mcp.server._parent_watch import start_parent_watch


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
    # PRD-CORE-248 FR02: the last thing this process controls before mcp.run()
    # takes over and the client's first frame decides what happens next.
    emit_boot_phase("transport_ready")
    start_parent_watch()
    mcp.run()
