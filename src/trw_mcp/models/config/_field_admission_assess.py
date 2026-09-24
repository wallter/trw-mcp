"""Admission records for the trw-jev fields: ``assess_enabled``, and ``transition_nudges_enabled``,
the switch over the transition selector FR06's Jev suggestions reach an agent through (PRD-CORE-294 FR05).

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

ASSESS_ADMISSIONS: dict[str, ConfigAdmission] = {
    "assess_enabled": ConfigAdmission(
        field_name="assess_enabled",
        owner="trw-jev slice 1",
        consumer=(
            "trw_mcp.middleware.surface_authority.SurfaceAuthorityMiddleware._resolve -> "
            "trw_mcp.server._surface_manifest_registry.resolve_tool_surface(assess_enabled=...); "
            "trw_mcp.tools.assess.trw_assess reads it directly for the disabled fast path"
        ),
        default_rationale=(
            "false. The tool registers either way, but with this off it is reachable only via "
            "trw_request_tool_access and its handler returns {status: disabled} with no network "
            "call and no judge construction, mirroring comms_enabled/dispatch_tools_exposed. "
            "As of 2026-09-23 this same field, read from a PROJECT's .trw/config.yaml, is also the "
            "project-scope layer of the backend's own enablement cascade "
            "(trw_memory.decisions.judge_from_env / resolve_backend_enablement: process env beats "
            "project scope beats the operator's ~/.trw/config.yaml beats off) -- so turning this on "
            "in a project CAN now create network egress once an OPENROUTER_API_KEY is also present, "
            "which relaxes the prior rule that a repo-controlled file could only ever disable it."
        ),
        interaction_analysis=(
            "Read once per surface resolution, alongside comms_enabled and dispatch_tools_exposed, "
            "and it only ADDS the assess_support pack to the bounded standard resolution -- it "
            "cannot narrow one, and it is not consulted at all under tool_resolution_mode='all'. "
            "It is dominated by surface_role='reviewer', which REPLACES the surface before any mode "
            "or pack is read, so it can never widen a dispatched reviewer (trw_assess is excluded "
            "from REVIEWER_TOOLS and STANDARD_TASK_PACKS by design). Turning it on changes the "
            "advertised catalogue, so a client that listed tools at connect needs the list_changed "
            "push the same middleware emits."
        ),
        deprecation_plan=(
            "Retire only if the assess_support pack graduates into a standard task pack or is "
            "removed; until then this is the sole surface-admission switch for trw_assess."
        ),
        docs_pointer=_ARCH_DOC,
        test_pointer="trw-mcp/tests/test_assess_tool.py",
        budget_decision="admitted",
    ),
    "transition_nudges_enabled": ConfigAdmission(
        field_name="transition_nudges_enabled",
        owner="PRD-CORE-294-FR05",
        consumer=(
            "trw_mcp.state._ceremony_nudge_selectors.transition_selector_enabled, the first check in "
            "select_transition_line, which every FR04/FR06 transition line passes through (the build-check "
            "and deliver transitions and the before-edit-hint transition); the trw_session_start tool_call "
            "event records it as nudge_selector.enabled for the engagement report"
        ),
        default_rationale=(
            "true on every client. The transition selector is what FR04 learning lines and FR06 Jev "
            "suggestions reach an agent through, so it must not follow nudge_enabled, which the codex and "
            "opencode profiles default to false; false is the ablation arm the FR05 on/off comparison "
            "needs, and nothing else sets it."
        ),
        interaction_analysis=(
            "Independent of nudge_enabled and the nudge pool: it gates only transition lines. With it off, "
            "select_transition_line returns None before reading or writing ceremony state, so the "
            "per-session budget, cooldown and shown-id dedup are untouched. FR06 Jev suggestions also need "
            "the Jev backend enabled; this switch off suppresses them whatever the backend says, and the "
            "recorded nudge_selector.jev is then false."
        ),
        deprecation_plan=(
            "Retire when the FR05 selector comparison has its matched cohorts and the operator decides "
            "whether transition lines stay unconditional."
        ),
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-294-learning-engagement.md#prd-core-294-fr05",
        test_pointer="trw-mcp/tests/test_tool_call_engagement_measures.py",
        budget_decision="admitted",
    ),
}

__all__ = ["ASSESS_ADMISSIONS"]
