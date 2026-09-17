"""The unmask step that every advisory naming an unreachable tool must carry.

PRD-FIX-140-FR06/FR07. Progressive disclosure (PRD-CORE-218) exposes a bounded
task surface — 15 of 53 tools in the 2026-09-16 probe — while server advisories
kept naming tools by their bare call syntax: ``call trw_pipeline_health() for
details``, ``Run trw_code_index_update for this repo first.``. An agent that
cannot see the tool has no way to know the advisory is actionable at all, and
``trw_request_tool_access`` (which DOES grant it in a live Claude Code session)
was named nowhere.

This module renders text and nothing else. It never grants, never widens a
surface and never writes to the override store: a disclosure repair must not
become an escalation path (NFR02).

Three classes get NO hint, because for them the step is a false promise:

* a tool already in the session's effective surface (including one reached
  through an active ``trw_request_tool_access`` grant);
* an ``OPERATOR_ONLY_TOOLS`` member, which the public agent surface excludes by
  policy rather than by masking;
* any session running under the reviewer role, where ``trw_request_tool_access``
  is itself neither listed nor callable (PRD-SEC-015).

Surface resolution is computed from the PURE membership module
(``models/surface_packs``) rather than by importing
``server._surface_manifest_registry``: importing anything under ``server``
initialises ``server/__init__``, which eagerly registers the tool surface — the
same hazard ``state/claude_md/_tool_manifest.py`` documents and avoids. Equality
with the real resolver is pinned by
*tests/test_fix140_masked_tool_advisory.py::test_resolution_matches_the_real_resolver*
for every task type, so this cannot drift into a second opinion.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


def effective_tool_surface(task_type: str | None, mode: str = "standard") -> frozenset[str]:
    """The tools a session for ``task_type`` can actually call.

    The CORE-218 resolution (kernel + the task's standard packs, or the
    ``unknown`` fallback for an unmapped task) unioned with the two sets every
    middleware layer adds back at the point of use: the never-hide
    ``RIGID_TOOLS`` and the bootstrap ``trw_init`` a no-run session needs to
    create its first run. ``mode="all"`` returns the full eligible public
    surface, matching the operator escape.
    """
    from trw_mcp.models.phase_policy import RIGID_TOOLS
    from trw_mcp.models.surface_packs import (
        OPERATOR_ONLY_TOOLS,
        PACK_TOOLS,
        STANDARD_TASK_PACKS,
    )

    if mode == "all":
        return frozenset(tool for tools in PACK_TOOLS.values() for tool in tools if tool not in OPERATOR_ONLY_TOOLS)
    selected = STANDARD_TASK_PACKS.get(task_type or "unknown", STANDARD_TASK_PACKS["unknown"])
    resolved = {tool for pack in ("kernel", *selected) for tool in PACK_TOOLS[pack]}
    return frozenset(resolved | set(RIGID_TOOLS) | {"trw_init"})


def unmask_hint(
    tool_name: str,
    *,
    reason: str,
    task_type: str | None = None,
    session_id: str | None = None,
) -> str:
    """The concrete grant step for ``tool_name``, or ``""`` when it is reachable.

    Returned text is a sentence fragment meant to be appended to an advisory, so
    an empty return leaves the advisory byte-identical to its pre-FR06 form.
    ``reason`` is echoed into the suggested call so the agent does not have to
    invent one.

    Fail-QUIET by design: any resolution error returns ``""``. A wrong
    instruction ("ask for a tool you already have", "ask for a tool nobody can
    grant") is worse than no instruction, and this text is never a gate.
    """
    if not tool_name.startswith("trw_"):
        return ""
    try:
        from trw_mcp.models.surface_packs import OPERATOR_ONLY_TOOLS
        from trw_mcp.state._surface_role import reviewer_role_active

        if tool_name in OPERATOR_ONLY_TOOLS or reviewer_role_active():
            return ""
        if session_id:
            from trw_mcp.tools.phase_overrides import has_active_override

            if has_active_override(session_id, tool_name):
                return ""
        from trw_mcp.models.config import get_config

        if tool_name in effective_tool_surface(task_type, str(get_config().tool_resolution_mode)):
            return ""
    # trw-fail-silent-allow: this renders ADVISORY TEXT, never a gate or a value a
    # caller branches on. A resolution fault must not turn an advisory into an
    # instruction to request a tool the session may already hold; the empty string
    # leaves the advisory byte-identical to its pre-FR06 form and the warning below
    # is the durable record.
    except Exception:  # justified: fail-quiet, a wrong disclosure hint is worse than none
        logger.warning("unmask_hint_unresolved", tool=tool_name, exc_info=True)
        # trw-fail-silent-allow: this renders ADVISORY TEXT, not a gate value; a wrong hint is worse than none
        return ""
    safe_reason = " ".join(reason.split())[:80] or "needed by this advisory"
    return (
        f" {tool_name} is not exposed in this session — call "
        f"trw_request_tool_access(tool_name='{tool_name}', reason='{safe_reason}') first."
    )
