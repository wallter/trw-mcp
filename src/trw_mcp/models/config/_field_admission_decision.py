"""Admission record for the trw-jev ``decision_enabled`` field.

Its own table module rather than one more entry in
``_field_admission_registry.py``: that file sits close to the 350
effective-LOC gate, so every domain that admits fields brings its own table
and the registry merges it (the pattern PRD-FIX-123 established).

Imports nothing from the rest of ``trw_mcp.models.config`` except the record
type, so it cannot create an import cycle with ``TRWConfig``.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

_ARCH_DOC = "docs/requirements-aare-f/prds/PRD-CORE-288-trw-jev-feature-flag.md"

DECISION_ADMISSIONS: dict[str, ConfigAdmission] = {
    "decision_enabled": ConfigAdmission(
        field_name="decision_enabled",
        owner="trw-jev slice 1",
        consumer=(
            "trw_mcp.middleware.surface_authority.SurfaceAuthorityMiddleware._resolve -> "
            "trw_mcp.server._surface_manifest_registry.resolve_tool_surface(decision_enabled=...); "
            "trw_mcp.tools.decision.trw_decision reads it directly for the disabled fast path"
        ),
        default_rationale=(
            "false. The tool registers either way, but with this off it is reachable only via "
            "trw_request_tool_access and its handler returns {status: disabled} with no network "
            "call and no judge construction, mirroring comms_enabled/dispatch_tools_exposed. "
            "Enabling the pack is independent of enabling the Jev backend itself: even with this "
            "true, trw_decision answers null/abstain unless TRW_JEV_ENABLED and "
            "OPENROUTER_API_KEY are ALSO set (trw_memory.decisions.judge_from_env), so turning "
            "this on cannot by itself create network egress."
        ),
        interaction_analysis=(
            "Read once per surface resolution, alongside comms_enabled and dispatch_tools_exposed, "
            "and it only ADDS the decision_support pack to the bounded standard resolution -- it "
            "cannot narrow one, and it is not consulted at all under tool_resolution_mode='all'. "
            "It is dominated by surface_role='reviewer', which REPLACES the surface before any mode "
            "or pack is read, so it can never widen a dispatched reviewer (trw_decision is excluded "
            "from REVIEWER_TOOLS and STANDARD_TASK_PACKS by design). Turning it on changes the "
            "advertised catalogue, so a client that listed tools at connect needs the list_changed "
            "push the same middleware emits."
        ),
        deprecation_plan=(
            "Retire only if the decision_support pack graduates into a standard task pack or is "
            "removed; until then this is the sole surface-admission switch for trw_decision."
        ),
        docs_pointer=_ARCH_DOC,
        test_pointer="trw-mcp/tests/test_decision_tool.py",
        budget_decision="admitted",
    ),
}

__all__ = ["DECISION_ADMISSIONS"]
