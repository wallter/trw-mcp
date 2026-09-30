"""TRW MCP Server -- orchestration, requirements, and self-learning tools.

Console entry point (``trw-mcp``). Importing this package builds no app: the served app, with its tools,
resources and prompts, is created by :func:`trw_mcp.server._app.build_served_app` when a transport starts.
Run with: ``trw-mcp`` CLI or ``trw-mcp --debug`` for file logging.

PRD-CORE-001: Base MCP tool suite.
"""

from __future__ import annotations

from trw_mcp._logging import configure_logging as _configure_logging

# The console_script entry point imports this package before ``main()`` runs.
# Configure a quiet stderr-only logger first so registration warnings never
# contaminate stdout for stdio MCP transports.
_configure_logging(
    debug=False,
    verbosity=0,
    log_level="CRITICAL",
    package_name="trw-mcp",
)


def main() -> None:
    from trw_mcp.server._cli import main as _main

    _main()


__all__ = ["main"]
