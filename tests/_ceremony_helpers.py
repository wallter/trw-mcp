"""Shared helpers for ceremony tool tests (DRY extraction)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.conftest import get_tools_sync


def make_ceremony_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> dict[str, Any]:
    """Create a FastMCP server with all ceremony-related tools and patched project root.

    Registers ceremony (session_start, deliver), checkpoint (pre_compact),
    and review tools so tests can access them all from a single server.
    Sets TRW_PROJECT_ROOT to tmp_path.
    """
    from fastmcp import FastMCP

    from trw_mcp.tools.ceremony import register_ceremony_tools
    from trw_mcp.tools.checkpoint import register_checkpoint_tools
    from trw_mcp.tools.review import register_review_tools

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

    srv = FastMCP("test")
    register_ceremony_tools(srv)
    register_checkpoint_tools(srv)
    register_review_tools(srv)

    return get_tools_sync(srv)
