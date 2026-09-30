"""SERVER-LAZY-APP-IMPORT: the served app is built explicitly, and importing the server builds nothing.

Importing ``trw_mcp.server`` used to create the FastMCP app and register every tool as a side effect, so every CLI
verb and every hook that entered through the ``trw-mcp`` console script paid for fastmcp and the whole tool graph
(~0.7 s of a bare import) before doing anything. Registration now happens in one place,
``trw_mcp.server._app.build_served_app()``, which the transports call.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_SRC = Path(__file__).resolve().parents[1] / "src"


async def test_the_served_app_registers_exactly_the_manifested_tool_surface() -> None:
    """Every tool in the surface manifest (the tool inventory's source) is served, and nothing else."""
    from trw_mcp.server._app import build_served_app
    from trw_mcp.server._surface_manifest_registry import MANIFEST_BY_NAME

    app = build_served_app()
    served = {tool.name for tool in await app._list_tools()}

    assert served == set(MANIFEST_BY_NAME)
    assert await app._list_resources(), "resources are registered on the served app"
    assert await app._list_prompts(), "prompts are registered on the served app"


async def test_each_build_is_a_fresh_fully_registered_app() -> None:
    """No hidden singleton: a second build is a distinct app with the same surface."""
    from trw_mcp.server._app import build_served_app

    first, second = build_served_app(), build_served_app()

    assert first is not second
    assert {t.name for t in await first._list_tools()} == {t.name for t in await second._list_tools()}


def test_a_created_app_is_empty_until_registered() -> None:
    """create_app() alone serves nothing: registration is the explicit step build_served_app() adds."""
    import asyncio

    from trw_mcp.server._app import create_app

    assert asyncio.run(create_app()._list_tools()) == []


_ENTRY_POINTS = r"""
import sys

def step(name, run):
    run()
    if "fastmcp" in sys.modules:
        print("FASTMCP " + name)
        raise SystemExit(0)

def cli_local_help():
    sys.argv = ["trw-mcp", "local", "--help"]
    from trw_mcp.server import main
    try:
        main()
    except SystemExit:
        pass

def manifest_seam():
    from trw_mcp.bootstrap._client_integrations import resolved_profile_from_manifest_seam
    assert resolved_profile_from_manifest_seam("coding") is not None

step("import trw_mcp.server", lambda: __import__("trw_mcp.server"))
step("trw-mcp local --help", cli_local_help)
step("auto-recall hook", lambda: __import__("trw_mcp.state._auto_recall_hook"))
step("before-edit hint hook", lambda: __import__("trw_mcp.tools._before_edit_hint_core"))
step("claude-code hook helpers", lambda: __import__("trw_mcp.channels.claude_code._hook_helpers"))
step("bootstrap manifest seam", manifest_seam)
print("CLEAN")
"""


def test_cli_and_hook_entry_points_never_import_fastmcp(tmp_path: Path) -> None:
    """A fresh interpreter walks the CLI and hook entry points; fastmcp must stay unloaded through all of them."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "PYTHONPATH": os.pathsep.join([str(_SRC), str(_SRC.parents[1] / "trw-memory" / "src")]),
        "MEMORY_DAEMON_AUTOSTART": "false",
        "TRW_MEMORY_DAEMON_AUTOSTART": "0",
    }
    result = subprocess.run(
        [sys.executable, "-c", _ENTRY_POINTS],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().splitlines()[-1] == "CLEAN", (
        f"an entry point loaded fastmcp: {result.stdout.strip().splitlines()[-1]}"
    )
