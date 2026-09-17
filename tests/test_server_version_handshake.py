"""PRD-FIX-141-FR01 — the MCP handshake reports the trw-mcp version.

``FastMCP("trw", ...)`` used to be constructed with no ``version=``, so the
``initialize`` reply carried FastMCP's OWN version in ``serverInfo.version``
(observed 3.4.7 against a 3.0.0 install, learning L-vITW). Every client-side
"which trw-mcp am I talking to" check read the wrong number, and no test could
see it because the value was never asserted anywhere.

The assertion is against the INSTALLED package metadata, not a hardcoded
golden: a literal would have to be bumped with every release and would pass
against a stale wheel, which is the exact failure mode this closes.
"""

from __future__ import annotations

import pytest


def _installed_version() -> str:
    """The version an MCP client should see for this checkout."""
    from trw_mcp import __version__

    return __version__


@pytest.mark.asyncio
async def test_handshake_serverinfo_version_is_the_trw_mcp_version() -> None:
    """A real in-process client handshake reports the trw-mcp package version."""
    from fastmcp import Client

    from trw_mcp.server._app import create_app

    app = create_app()
    async with Client(app) as client:  # type: ignore[arg-type]
        result = client.initialize_result

    assert result.serverInfo.name == "trw"
    assert result.serverInfo.version == _installed_version()


@pytest.mark.asyncio
async def test_handshake_version_is_not_the_fastmcp_library_version() -> None:
    """The reported version is trw-mcp's, not the transport library's.

    Guards the specific regression: FastMCP defaults ``serverInfo.version`` to
    its own package version when the caller passes none, and the two numbers are
    both plausible three-part strings, so only a direct comparison catches it.
    """
    import fastmcp
    from fastmcp import Client

    from trw_mcp.server._app import create_app

    fastmcp_version = getattr(fastmcp, "__version__", "")
    app = create_app()
    async with Client(app) as client:  # type: ignore[arg-type]
        reported = client.initialize_result.serverInfo.version

    assert reported == _installed_version()
    if fastmcp_version and fastmcp_version != _installed_version():
        assert reported != fastmcp_version


def test_installed_version_resolves_from_package_metadata() -> None:
    """``trw_mcp.__version__`` is derived, never a literal in ``_app.py``.

    Reading the source keeps the FR honest: passing ``version="3.0.0"`` would
    satisfy both handshake assertions above while reintroducing exactly the
    drift this requirement removes.
    """
    from pathlib import Path

    import trw_mcp.server._app as app_module

    source = Path(app_module.__file__).read_text(encoding="utf-8")
    assert "version=__version__," in source
