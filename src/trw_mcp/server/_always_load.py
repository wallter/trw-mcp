"""Deferred-loading metadata: which tools load BEFORE the agent can search.

WHY THIS EXISTS
---------------
Claude Code defers **all** MCP tool schemas by default (``ENABLE_TOOL_SEARCH``
unset = defer). At session start the model receives only tool *names* and the
server ``instructions``; every schema is fetched on demand through a ToolSearch
round-trip. That is a ~96% cut in tool-definition context, and it is free.

It is not free for the ceremony floor. ``trw_session_start`` is the mandated
first action of every session, so under blanket deferral every session pays a
search round-trip *before it can begin* — a guaranteed tax on the exact call the
framework depends on most. The same applies to the small set of calls an agent
makes opportunistically mid-flight: an agent that must stop and search to record
a checkpoint or a learning is precisely the agent that will skip it, and the
skipped call is invisible.

Claude Code's escape hatch is per-tool ``"anthropic/alwaysLoad": true`` in the
tool's ``_meta``. FastMCP 3.2.4 plumbs a tool's ``meta`` dict straight through
``Tool.to_mcp_tool()`` into the wire ``_meta`` object (verified 2026-07-28
against the live server: ``{"anthropic/alwaysLoad": true, "fastmcp": {...}}``),
so no fork or transport shim is needed.

WHAT THE FLOOR IS (PRD-CORE-300-FR14)
------------------------------------
Every name here is a tool deferral no longer saves, so the set is not a taste
list. After the PRD-CORE-300 cut the always-on kernel is small (the tools an
agent needs in every session: session start, init, status, recall, learn,
checkpoint, deliver, build check, review, PRD validation, code navigation), and
a kernel tool an agent must stop and search for is a kernel tool it skips. So
the floor IS the always-on kernel, read from ``models/surface_v2.POST_CUT_KERNEL``
(the spec S11b makes ``surface_packs.KERNEL_TOOLS`` equal) rather than restated
here. A kernel name that is not registered yet, ``trw_code`` before slice S10,
is skipped and logged, and joins the floor the moment it registers.

Flag-gated, outside the kernel:

* ``trw_assess`` (``assess_enabled``), ``trw_send`` and ``trw_inbox``
  (``comms_enabled``) — loaded up front only when the flag is on. A project
  that opted into the advisory judge or a formation wants them used, and a
  deferred tool was the reason two lanes gave for not reaching for the judge.
  With the flag off they stay deferred, so no install pays for them unasked.
* ``trw_dispatch`` — NEVER loaded up front, whatever ``dispatch_tools_exposed``
  says. It spawns other agents; reaching it should cost a deliberate search.

The per-server ``alwaysLoad`` in ``.mcp.json`` is the WRONG lever for this and is
not used: it exempts the entire server from deferral, which restores the whole
~15.7k-token definition surface and discards the benefit this module exists to
keep, and exempts an exposed ``trw_dispatch`` too. Per-tool ``_meta`` is the
fine-grained control; the generated config never sets the per-server key.

Clients that ignore ``_meta`` (every upfront-loading client: opencode, Codex
CLI, Cursor, Cline, Gemini CLI) are unaffected — the key is additive metadata,
not a schema change, so it cannot break a client that does not read it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

import structlog

from trw_mcp.models.surface_v2 import POST_CUT_FLAGGED, POST_CUT_KERNEL

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = structlog.get_logger(__name__)

#: Claude Code's per-tool deferral opt-out key, written into the tool's ``_meta``.
ALWAYS_LOAD_META_KEY: Final[str] = "anthropic/alwaysLoad"

#: The always-on kernel, loaded before the agent can search for anything.
#: Derived from the post-cut kernel spec; never hand-list a name here.
ALWAYS_LOAD_TOOLS: Final[frozenset[str]] = frozenset(POST_CUT_KERNEL)

#: Flag-gated tools that are never loaded up front, whatever their flag says.
NEVER_ALWAYS_LOAD: Final[frozenset[str]] = frozenset({"trw_dispatch"})

#: tool -> config flag: loaded up front only while that flag is true.
FLAG_GATED_ALWAYS_LOAD: Final[Mapping[str, str]] = {
    tool: flag for tool, flag in POST_CUT_FLAGGED.items() if tool not in NEVER_ALWAYS_LOAD
}

#: The config flags the boot path must read (see ``server/_tools.py``).
GATING_FLAGS: Final[frozenset[str]] = frozenset(FLAG_GATED_ALWAYS_LOAD.values())


def always_load_names(flags: Mapping[str, bool]) -> frozenset[str]:
    """The kernel floor plus each flag-gated tool whose flag is on in *flags*."""
    return ALWAYS_LOAD_TOOLS | {tool for tool, flag in FLAG_GATED_ALWAYS_LOAD.items() if flags.get(flag, False)}


async def apply_always_load_meta(server: FastMCP, *, flags: Mapping[str, bool] | None = None) -> tuple[str, ...]:
    """Mark the kernel floor, plus each flag-gated tool whose flag is on, as always-loaded.

    *flags* maps a config flag name (``assess_enabled``, ``comms_enabled``) to
    its value; a missing flag counts as off.

    Mutates the registered tool objects in place. That is intentional and safe
    here, and only here: ``get_tool`` returns the registry singleton, so the
    assignment is process-permanent (learning L-MZ6J). This runs exactly once at
    boot with a config-derived set, which is the one shape where permanence is the goal
    — a per-request mutation of the same objects would be a leak.

    Fail-open per tool: a name that does not resolve is logged and skipped, so a
    rename or a gated registrar can never brick startup.

    Returns:
        The tool names actually marked, sorted — the caller's evidence that the
        floor was applied rather than silently lost.
    """
    applied: list[str] = []
    missing: list[str] = []

    for name in sorted(always_load_names(flags or {})):
        tool: Any = None
        try:
            tool = await server.get_tool(name)
        except Exception:  # justified: fail-open, a boot-path lookup must not abort startup
            tool = None
        if tool is None:
            missing.append(name)
            continue
        meta: dict[str, Any] = dict(getattr(tool, "meta", None) or {})
        meta[ALWAYS_LOAD_META_KEY] = True
        tool.meta = meta
        applied.append(name)

    if missing:
        # info, not debug: a default install emits nothing at debug level, and
        # a silently-unapplied floor is exactly the failure worth a record.
        logger.info("always_load_tools_unresolved", tools=missing)

    return tuple(applied)


__all__ = [
    "ALWAYS_LOAD_META_KEY",
    "ALWAYS_LOAD_TOOLS",
    "FLAG_GATED_ALWAYS_LOAD",
    "GATING_FLAGS",
    "NEVER_ALWAYS_LOAD",
    "always_load_names",
    "apply_always_load_meta",
]
