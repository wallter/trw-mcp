"""The production served app, built once per test process.

Importing ``trw_mcp.server`` no longer builds or registers anything (SERVER-LAZY-APP-IMPORT); the served app comes
from :func:`trw_mcp.server._app.build_served_app`. Tests that read the served surface share one build per process,
as they shared the import-time singleton before, instead of paying ~0.7 s per call site.
"""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP


@cache
def served_app() -> FastMCP:
    from trw_mcp.server._app import build_served_app

    return build_served_app()
