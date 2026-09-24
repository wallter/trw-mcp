"""Decision-support config field (trw-jev, opt-in, off by default).

One bool, read for two purposes now (2026-09-23 operator decision). It admits the
``assess_support`` capability pack onto a session's resolved tool surface (``trw_assess``), and
the SAME key in a project's ``.trw/config.yaml`` is also read by
``trw_memory.decisions._enablement.resolve_backend_enablement`` as the project-scope layer of the
backend's own on/off cascade: process env beats project scope (this field, or ``TRW_JEV_ENABLED``
in the project ``.env``) beats user scope (``assess_enabled`` in ``~/.trw/config.yaml``) beats off.
So a project MAY now enable the backend from this one field (previously it could only surface the
tool and never enable egress) -- it still needs an ``OPENROUTER_API_KEY`` to actually reach a
provider, and the base URL stays restricted to an allowlisted host regardless of which layer
enabled it. See the trw-jev decision-backend design (PRD-CORE-288).
"""

from __future__ import annotations

from pydantic import Field


class _AssessFields:
    """Decision-support domain mixin — mixed into _TRWConfigFields via MI."""

    #: Expose the ``assess_support`` capability pack (``trw_assess``) on
    #: the resolved MCP tool surface. OFF by default: the tool ships
    #: registered either way, but with this false it is reachable only via
    #: trw_request_tool_access, mirroring comms_enabled/dispatch_tools_exposed.
    #: Read by ``middleware/surface_authority`` -> ``resolve_tool_surface``.
    assess_enabled: bool = Field(
        default=False,
        description=(
            "Expose the assess_support capability pack (trw_assess) on the resolved tool "
            "surface. Off by default; otherwise reachable only via trw_request_tool_access. This "
            "same field, in a project's .trw/config.yaml, ALSO now counts as the project-scope "
            "layer of the backend's own enablement cascade (2026-09-23) -- process env beats "
            "project scope beats the operator's ~/.trw/config.yaml beats off -- so trw_assess "
            "answers from the null/rules path only when no layer says on, or an OPENROUTER_API_KEY "
            "is still missing (trw_memory.decisions.judge_from_env / resolve_backend_enablement)."
        ),
    )


__all__ = ["_AssessFields"]
