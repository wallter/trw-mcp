"""Decision-support config field (trw-jev, opt-in, off by default).

One bool. It only admits the ``decision_support`` capability pack onto a
session's resolved tool surface (``trw_decision``) — it does not itself turn
on network egress. The Jev backend is a further, independent opt-in resolved
at call time by ``trw_memory.decisions.judge_from_env`` (``TRW_JEV_ENABLED`` +
``OPENROUTER_API_KEY``), so a project can expose the tool while still getting
only the rules/null answer path. See the trw-jev decision-backend design
(PRD-CORE-288).
"""

from __future__ import annotations

from pydantic import Field


class _DecisionFields:
    """Decision-support domain mixin — mixed into _TRWConfigFields via MI."""

    #: Expose the ``decision_support`` capability pack (``trw_decision``) on
    #: the resolved MCP tool surface. OFF by default: the tool ships
    #: registered either way, but with this false it is reachable only via
    #: trw_request_tool_access, mirroring comms_enabled/dispatch_tools_exposed.
    #: Read by ``middleware/surface_authority`` -> ``resolve_tool_surface``.
    decision_enabled: bool = Field(
        default=False,
        description=(
            "Expose the decision_support capability pack (trw_decision) on the resolved tool "
            "surface. Off by default; otherwise reachable only via trw_request_tool_access. Even "
            "when true, trw_decision answers from the null/rules path unless the operator ALSO "
            "sets TRW_JEV_ENABLED and OPENROUTER_API_KEY (trw_memory.decisions.judge_from_env)."
        ),
    )


__all__ = ["_DecisionFields"]
