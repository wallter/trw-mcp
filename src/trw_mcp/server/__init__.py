"""TRW MCP Server -- orchestration, requirements, and self-learning tools.

FastMCP server entry point. Registers all tools, resources, and prompts.
Run with: ``trw-mcp`` CLI or ``trw-mcp --debug`` for file logging.

PRD-CORE-001: Base MCP tool suite.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from trw_mcp._logging import configure_logging as _configure_logging

# The console_script entry point imports this package before ``main()`` runs.
# Configure a quiet stderr-only logger first so eager registration warnings
# never contaminate stdout for stdio MCP transports.
_configure_logging(
    debug=False,
    verbosity=0,
    log_level="CRITICAL",
    package_name="trw-mcp",
)

def _load_mcp() -> object:
    from trw_mcp.server._app import mcp as _mcp

    return _mcp


def _check_mcp_json_portability(cwd: Path | None = None) -> None:
    from trw_mcp.server._cli import _check_mcp_json_portability as _portability_check

    _portability_check(cwd)


def main() -> None:
    from trw_mcp.server._cli import main as _main

    _main()


def _load_register_tools() -> Callable[[], None]:
    from trw_mcp.server._tools import _register_tools as _register

    return _register


mcp = _load_mcp()

# Import _tools first to trigger eager tool registration (side effect).
_register_tools = _load_register_tools()

__all__ = ["_check_mcp_json_portability", "main", "mcp"]
