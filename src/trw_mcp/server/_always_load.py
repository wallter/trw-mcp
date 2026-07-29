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

WHY THIS IS A FLOOR AND NOT A LIST
----------------------------------
Every name here is a tool deferral no longer saves. The set is therefore capped
by intent, not by taste: a tool earns a place only if *not finding it* silently
degrades the framework, and only if nothing already-loaded can substitute.

Admitted (5):

* ``trw_session_start`` — the mandated first action. Nothing precedes it, so
  nothing can tell the agent to search for it. The headline case.
* ``trw_checkpoint`` — the compaction-survival call. It must fire *before* an
  unpredictable event, mid-implementation, on no external prompt.
* ``trw_learn`` — the capture path for the value hierarchy's third rung. Fires
  opportunistically on discovery, with the same "won't stop to search" failure
  mode as the checkpoint.
* ``trw_build_check`` — the evidence ``trw_deliver``'s gate reads. Loading the
  gate but not its precondition strands the agent at the gate.
* ``trw_deliver`` — the terminal ceremony. Missing it discards the session.

Deliberately EXCLUDED, and why the exclusion holds:

* ``trw_recall`` — kernel, but ``trw_session_start(query=...)`` already performs
  the recall, and that tool is loaded. Subsumed.
* ``trw_review`` — RIGID, and the remedy for the ``review_scope_block``
  NO_ESCAPE. But that block names ``trw_review`` in its own text, and the agent
  reading it is already stopped: it can afford one search, with an exact name.
* ``trw_status`` / ``trw_skill_discovery`` / ``trw_request_tool_access`` /
  ``trw_profile_explain`` — kernel, but each is reached deliberately, when the
  agent has already decided it wants them. On-demand is the correct cost.

The per-server ``alwaysLoad`` in ``.mcp.json`` is the WRONG lever for this and is
not used: it exempts the entire server from deferral, which restores the whole
~15.7k-token definition surface and discards the benefit this module exists to
keep. Per-tool ``_meta`` is the fine-grained control.

Clients that ignore ``_meta`` (every upfront-loading client: opencode, Codex
CLI, Cursor, Cline, Gemini CLI) are unaffected — the key is additive metadata,
not a schema change, so it cannot break a client that does not read it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

import structlog

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = structlog.get_logger(__name__)

#: Claude Code's per-tool deferral opt-out key, written into the tool's ``_meta``.
ALWAYS_LOAD_META_KEY: Final[str] = "anthropic/alwaysLoad"

#: The ceremony floor that loads before the agent can search for anything.
#: Read this module's docstring before adding a name — each addition is a tool
#: whose definition every deferring client pays for in every session.
ALWAYS_LOAD_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "trw_session_start",
        "trw_checkpoint",
        "trw_learn",
        "trw_build_check",
        "trw_deliver",
    }
)


async def apply_always_load_meta(server: FastMCP) -> tuple[str, ...]:
    """Mark every :data:`ALWAYS_LOAD_TOOLS` entry as always-loaded on ``server``.

    Mutates the registered tool objects in place. That is intentional and safe
    here, and only here: ``get_tool`` returns the registry singleton, so the
    assignment is process-permanent (learning L-MZ6J). This runs exactly once at
    boot with a static set, which is the one shape where permanence is the goal
    — a per-request mutation of the same objects would be a leak.

    Fail-open per tool: a name that does not resolve is logged and skipped, so a
    rename or a gated registrar can never brick startup.

    Returns:
        The tool names actually marked, sorted — the caller's evidence that the
        floor was applied rather than silently lost.
    """
    applied: list[str] = []
    missing: list[str] = []

    for name in sorted(ALWAYS_LOAD_TOOLS):
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
    "apply_always_load_meta",
]
