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
3. The floor is DERIVED from the post-cut kernel spec (PRD-CORE-300-FR14), never
   a second hand list, so ``trw_code`` joins it the moment it is registered.
4. Flag-gated tools (``trw_assess``, ``trw_send``, ``trw_inbox``) always carry
   the key, whatever their flag said at boot, so a flag turned on mid-session
   surfaces them loaded (PRD-CORE-305-FR04); the surface mask alone decides
   whether they are listed. ``trw_dispatch`` never carries it.
5. The generated Claude Code ``.mcp.json`` sets no per-server ``alwaysLoad``: that
   lever exempts every registered tool, an exposed ``trw_dispatch`` included.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

pytestmark = pytest.mark.unit


async def _wire_meta() -> dict[str, dict[str, Any]]:
    """Return ``{tool_name: _meta}`` exactly as the MCP client receives it."""
    from trw_mcp.server._app import mcp

    out: dict[str, dict[str, Any]] = {}
    for tool in await mcp._list_tools():
        dumped = tool.to_mcp_tool().model_dump(by_alias=True, exclude_none=True)
        out[str(dumped.get("name") or "")] = dict(dumped.get("_meta") or {})
    return out


async def _restoring(names: frozenset[str]) -> dict[str, dict[str, Any]]:
    """Snapshot ``Tool.meta`` for *names* (the registry is a process singleton)."""
    from trw_mcp.server._app import mcp

    saved: dict[str, dict[str, Any]] = {}
    for name in names:
        tool = await mcp.get_tool(name)
        if tool is not None:
            saved[name] = dict(tool.meta or {})
    return saved


async def _restore(saved: dict[str, dict[str, Any]]) -> None:
    from trw_mcp.server._app import mcp

    for name, meta in saved.items():
        tool = await mcp.get_tool(name)
        tool.meta = meta


def _kernel() -> frozenset[str]:
    from trw_mcp.models.surface_v2 import POST_CUT_KERNEL

    return frozenset(POST_CUT_KERNEL)


def test_the_floor_is_the_always_on_kernel_derived_not_listed() -> None:
    """FR14: the floor IS the post-cut kernel spec, read from it, not restated."""
    from trw_mcp.server._always_load import ALWAYS_LOAD_TOOLS

    assert ALWAYS_LOAD_TOOLS == _kernel()


def test_flag_gated_tools_are_derived_and_dispatch_is_never_among_them() -> None:
    """FR14: assess/send/inbox are the flag-gated marked set; trw_dispatch never carries the key."""
    from trw_mcp.models.surface_v2 import POST_CUT_FLAGGED
    from trw_mcp.server._always_load import (
        ALWAYS_LOAD_TOOLS,
        FLAG_GATED_ALWAYS_LOAD,
        NEVER_ALWAYS_LOAD,
    )

    assert NEVER_ALWAYS_LOAD == frozenset({"trw_dispatch"})
    assert FLAG_GATED_ALWAYS_LOAD == {
        "trw_assess": "assess_enabled",
        "trw_send": "comms_enabled",
        "trw_inbox": "comms_enabled",
    }
    assert {**FLAG_GATED_ALWAYS_LOAD, **{t: POST_CUT_FLAGGED[t] for t in NEVER_ALWAYS_LOAD}} == POST_CUT_FLAGGED
    assert not NEVER_ALWAYS_LOAD & ALWAYS_LOAD_TOOLS


async def test_every_registered_kernel_tool_is_always_loaded_on_the_wire() -> None:
    """Every registered always-on kernel tool ships the opt-out in its serialized ``_meta``."""
    from trw_mcp.server._always_load import ALWAYS_LOAD_META_KEY

    meta = await _wire_meta()
    registered_kernel = _kernel() & set(meta)
    assert registered_kernel, "no kernel tool registered, the check below would be vacuous"
    missing = sorted(n for n in registered_kernel if meta[n].get(ALWAYS_LOAD_META_KEY) is not True)
    assert not missing, (
        f"These kernel tools are NOT marked always-loaded on the wire: {missing}. "
        "Under Claude Code's default deferral an agent must spend a ToolSearch "
        "round-trip before it can call them. Check that _register_tools() still calls "
        "_apply_always_load_meta() and that FastMCP still plumbs Tool.meta "
        "into the wire _meta object."
    )


async def test_nothing_outside_the_floor_is_marked_by_default() -> None:
    """Control: marking the whole surface always-loaded would defeat deferral."""
    from trw_mcp.server._always_load import ALWAYS_LOAD_META_KEY, ALWAYS_LOAD_TOOLS, FLAG_GATED_ALWAYS_LOAD

    meta = await _wire_meta()
    assert meta, "no tools registered, the rest of this file would be vacuous"
    over_marked = sorted(
        name
        for name, m in meta.items()
        if m.get(ALWAYS_LOAD_META_KEY) is True and name not in ALWAYS_LOAD_TOOLS | set(FLAG_GATED_ALWAYS_LOAD)
    )
    assert not over_marked, (
        f"Tools marked always-loaded outside the kernel floor and its flag-gated tools: {over_marked}. "
        "Every always-loaded tool is one deferral no longer saves."
    )
    assert "trw_dispatch" in meta, "trw_dispatch is not registered; the never-marked control is vacuous"
    assert ALWAYS_LOAD_META_KEY not in meta["trw_dispatch"]


async def test_apply_preserves_existing_meta_and_is_idempotent() -> None:
    """Re-applying must not clobber FastMCP's own ``_meta`` namespace or duplicate."""
    from trw_mcp.server._always_load import ALWAYS_LOAD_META_KEY, always_load_names, apply_always_load_meta
    from trw_mcp.server._app import mcp

    meta_before = await _wire_meta()
    applied = await apply_always_load_meta(mcp)
    expected = always_load_names() & set(meta_before)
    assert set(applied) == expected, f"applied {sorted(applied)} != registered floor {sorted(expected)}"

    meta = await _wire_meta()
    for name in expected:
        assert meta[name].get(ALWAYS_LOAD_META_KEY) is True
        # FastMCP writes its own namespace into the same object; a naive
        # `tool.meta = {KEY: True}` would silently drop it.
        assert "fastmcp" in meta[name], f"{name}: applying the opt-out destroyed FastMCP's own _meta namespace"


class _StubTool:
    def __init__(self) -> None:
        self.meta: dict[str, Any] | None = None


class _StubServer:
    """A server that registers exactly *names*: stands in for a future registry."""

    def __init__(self, names: set[str]) -> None:
        self.tools = {name: _StubTool() for name in names}

    async def get_tool(self, name: str) -> object | None:
        return self.tools.get(name)


async def test_trw_code_joins_the_floor_automatically_once_registered() -> None:
    """FR14: trw_code carries the key only once registered, with no edit to this module."""
    from trw_mcp.server._always_load import ALWAYS_LOAD_META_KEY, apply_always_load_meta

    without = await apply_always_load_meta(_StubServer(set(_kernel() - {"trw_code"})))  # type: ignore[arg-type]
    assert "trw_code" not in without
    assert set(without) == _kernel() - {"trw_code"}

    server = _StubServer(set(_kernel()) | {"trw_dispatch"})
    applied = await apply_always_load_meta(server)  # type: ignore[arg-type]
    assert "trw_code" in applied
    assert server.tools["trw_code"].meta == {ALWAYS_LOAD_META_KEY: True}
    assert server.tools["trw_dispatch"].meta is None


async def test_unresolvable_tool_name_is_survivable() -> None:
    """A renamed or gated tool must degrade to a logged skip, never a boot failure."""
    from trw_mcp.server import _always_load

    applied = await _always_load.apply_always_load_meta(_StubServer(set()))  # type: ignore[arg-type]
    assert applied == (), "an unresolvable floor must yield no applications, not an exception"


_FLAG_CASES = [
    ("trw_assess", "assess_enabled"),
    ("trw_send", "comms_enabled"),
    ("trw_inbox", "comms_enabled"),
]


@pytest.mark.parametrize(("tool_name", "flag"), _FLAG_CASES, ids=[t for t, _ in _FLAG_CASES])
async def test_flag_gated_tool_is_marked_whatever_its_boot_flag(
    tool_name: str, flag: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRD-CORE-305-FR04: the boot hook marks a flag-gated tool even with its flag OFF.

    Marking only the tools whose flag was on at boot went stale: turning the flag
    on mid-session surfaced the tool without the key, deferred until a restart.
    The surface mask hides it while the flag is off, so the key costs nothing.
    """
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server import _tools
    from trw_mcp.server._always_load import ALWAYS_LOAD_META_KEY
    from trw_mcp.server._app import mcp

    saved = await _restoring(frozenset({tool_name, "trw_dispatch"}))
    try:
        (await mcp.get_tool(tool_name)).meta = {k: v for k, v in saved[tool_name].items() if k != ALWAYS_LOAD_META_KEY}
        cfg = TRWConfig(**{flag: False, "dispatch_tools_exposed": True})
        monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: cfg)
        monkeypatch.delenv("TRW_JEV_ENABLED", raising=False)
        await asyncio.to_thread(_tools._apply_always_load_meta)
        wire = await _wire_meta()
    finally:
        await _restore(saved)

    assert wire[tool_name].get(ALWAYS_LOAD_META_KEY) is True, f"{tool_name} unmarked with {flag} off at boot"
    assert ALWAYS_LOAD_META_KEY not in wire["trw_dispatch"], "trw_dispatch must never carry the key, even when exposed"


async def test_a_flag_enabled_mid_session_lists_its_tool_always_loaded(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRD-CORE-305-FR04 end to end: boot with comms off, turn it on in the file,
    and the next list shows ``trw_send`` WITH the key, no restart."""
    from trw_mcp.middleware.surface_authority import SurfaceAuthorityMiddleware, reset_surface_authority_state
    from trw_mcp.models.config import reload_config
    from trw_mcp.server import _tools
    from trw_mcp.server._always_load import ALWAYS_LOAD_META_KEY
    from trw_mcp.server._app import mcp

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("TRW_COMMS_ENABLED", raising=False)
    config = tmp_path / ".trw" / "config.yaml"
    config.parent.mkdir()
    config.write_text("comms_enabled: false\n", encoding="utf-8")
    reload_config()
    reset_surface_authority_state()
    saved = await _restoring(frozenset({"trw_send"}))
    middleware = SurfaceAuthorityMiddleware()

    class _Ctx:
        fastmcp_context = None

    async def listed() -> dict[str, Any]:
        async def call_next(_ctx: Any) -> Any:
            return await mcp._list_tools()

        return {t.name: t for t in await middleware.on_list_tools(_Ctx(), call_next)}  # type: ignore[arg-type]

    try:
        (await mcp.get_tool("trw_send")).meta = {
            k: v for k, v in saved["trw_send"].items() if k != ALWAYS_LOAD_META_KEY
        }
        await asyncio.to_thread(_tools._apply_always_load_meta)
        assert "trw_send" not in await listed()
        config.write_text("comms_enabled: true\n", encoding="utf-8")
        now = await listed()
        assert "trw_send" in now
        wire = now["trw_send"].to_mcp_tool().model_dump(by_alias=True, exclude_none=True)
        assert (wire.get("_meta") or {}).get(ALWAYS_LOAD_META_KEY) is True
    finally:
        await _restore(saved)
        reset_surface_authority_state()
        reload_config()


def test_generated_claude_code_mcp_config_sets_no_per_server_always_load(tmp_path: Any) -> None:
    """FR14: the per-server lever exempts every registered tool, trw_dispatch included."""
    import json
    from pathlib import Path

    import trw_mcp.bootstrap._mcp_json as mcp_json

    source = Path(mcp_json.__file__).read_text(encoding="utf-8")
    assert "alwaysLoad" not in source  # the PRD's grep_absent assertion

    result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}
    mcp_json._merge_mcp_json(tmp_path, result)
    written = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    assert "alwaysLoad" not in written["mcpServers"]["trw"]
