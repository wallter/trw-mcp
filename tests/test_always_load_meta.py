"""The ceremony floor must reach the wire as ``_meta."anthropic/alwaysLoad"``.

WHY THIS EXISTS

Claude Code defers every MCP tool schema by default: at session start the model
receives tool *names* and the server ``instructions``, and nothing else. Any
tool it needs must first be fetched through a ToolSearch round-trip.

For ``trw_session_start`` that ordering is circular in practice — it is the
mandated first action, so the framework's most load-bearing call is the one
that pays the discovery tax. The per-tool opt-out is a single key in the tool's
``_meta``, and the failure mode if it stops being emitted is *completely
silent*: the server still works, the tools still resolve, agents just pay a
round-trip forever and nothing logs it. Hence a wire-level test.

WHAT IS PINNED, AND WHY EACH ASSERTION IS NON-VACUOUS

1. The key reaches the SERIALIZED tool, via ``to_mcp_tool()`` — not the FastMCP
   ``Tool.meta`` attribute we set. Asserting on the attribute we just assigned
   would pass even if FastMCP stopped plumbing ``meta`` into ``_meta``, which is
   the upstream regression most worth catching.
2. A control asserts some registered tool does NOT carry the key. Without it,
   marking the entire surface always-loaded — which destroys the ~96% deferral
   saving this module exists to preserve — would pass every other assertion.
3. The floor is capped. The set is a cost, not a convenience list: each entry is
   a definition every deferring client pays for in every session.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.unit

# Read this file's module docstring in ``server/_always_load.py`` before raising
# this. Five is the argued cap, not a round number.
MAX_ALWAYS_LOADED_TOOLS = 5


async def _wire_meta() -> dict[str, dict[str, Any]]:
    """Return ``{tool_name: _meta}`` exactly as the MCP client receives it."""
    from trw_mcp.server._app import mcp

    out: dict[str, dict[str, Any]] = {}
    for tool in await mcp._list_tools():
        dumped = tool.to_mcp_tool().model_dump(by_alias=True, exclude_none=True)
        out[str(dumped.get("name") or "")] = dict(dumped.get("_meta") or {})
    return out


async def test_ceremony_floor_is_always_loaded_on_the_wire() -> None:
    """Every ALWAYS_LOAD_TOOLS entry ships the opt-out in its serialized ``_meta``."""
    from trw_mcp.server._always_load import ALWAYS_LOAD_META_KEY, ALWAYS_LOAD_TOOLS

    meta = await _wire_meta()
    missing = sorted(name for name in ALWAYS_LOAD_TOOLS if meta.get(name, {}).get(ALWAYS_LOAD_META_KEY) is not True)
    assert not missing, (
        f"These tools are NOT marked always-loaded on the wire: {missing}. "
        "Under Claude Code's default deferral an agent must spend a ToolSearch "
        "round-trip before it can call them — for trw_session_start that is a "
        "tax on every session. Check that _register_tools() still calls "
        "_apply_always_load_meta() and that FastMCP still plumbs Tool.meta "
        "into the wire _meta object."
    )


async def test_deferral_is_still_the_default_for_the_rest_of_the_surface() -> None:
    """Control: marking everything always-loaded would defeat the point."""
    from trw_mcp.server._always_load import ALWAYS_LOAD_META_KEY, ALWAYS_LOAD_TOOLS

    meta = await _wire_meta()
    assert meta, "no tools registered — the rest of this file would be vacuous"
    over_marked = sorted(
        name for name, m in meta.items() if m.get(ALWAYS_LOAD_META_KEY) is True and name not in ALWAYS_LOAD_TOOLS
    )
    assert not over_marked, (
        f"Tools marked always-loaded but absent from ALWAYS_LOAD_TOOLS: {over_marked}. "
        "Every always-loaded tool is one deferral no longer saves."
    )
    deferred = [name for name, m in meta.items() if ALWAYS_LOAD_META_KEY not in m]
    assert deferred, "EVERY registered tool is always-loaded — that discards the deferral saving entirely"


async def test_always_load_floor_is_registered_and_capped() -> None:
    """The floor names real tools, and stays small enough to be worth having."""
    from trw_mcp.server._always_load import ALWAYS_LOAD_TOOLS

    meta = await _wire_meta()
    unregistered = sorted(ALWAYS_LOAD_TOOLS - set(meta))
    assert not unregistered, f"ALWAYS_LOAD_TOOLS names tools that are not registered: {unregistered}"
    assert len(ALWAYS_LOAD_TOOLS) <= MAX_ALWAYS_LOADED_TOOLS, (
        f"{len(ALWAYS_LOAD_TOOLS)} always-loaded tools exceeds the cap of "
        f"{MAX_ALWAYS_LOADED_TOOLS}. Raising the cap means every deferring "
        "client pays another full definition in every session — argue it in "
        "server/_always_load.py, not here."
    )


async def test_apply_preserves_existing_meta_and_is_idempotent() -> None:
    """Re-applying must not clobber FastMCP's own ``_meta`` namespace or duplicate."""
    from trw_mcp.server._always_load import (
        ALWAYS_LOAD_META_KEY,
        ALWAYS_LOAD_TOOLS,
        apply_always_load_meta,
    )
    from trw_mcp.server._app import mcp

    applied = await apply_always_load_meta(mcp)
    assert set(applied) == ALWAYS_LOAD_TOOLS, f"applied {sorted(applied)} != floor {sorted(ALWAYS_LOAD_TOOLS)}"

    meta = await _wire_meta()
    for name in ALWAYS_LOAD_TOOLS:
        assert meta[name].get(ALWAYS_LOAD_META_KEY) is True
        # FastMCP writes its own namespace into the same object; a naive
        # `tool.meta = {KEY: True}` would silently drop it.
        assert "fastmcp" in meta[name], f"{name}: applying the opt-out destroyed FastMCP's own _meta namespace"


async def test_unresolvable_tool_name_is_survivable() -> None:
    """A renamed or gated tool must degrade to a logged skip, never a boot failure."""
    from trw_mcp.server import _always_load

    class _EmptyServer:
        async def get_tool(self, name: str) -> object | None:
            return None

    applied = await _always_load.apply_always_load_meta(_EmptyServer())  # type: ignore[arg-type]
    assert applied == (), "an unresolvable floor must yield no applications, not an exception"
