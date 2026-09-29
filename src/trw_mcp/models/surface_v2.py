"""The post-cut MCP tool surface, stated once (PRD-CORE-300-FR01, slice S0).

PRD-CORE-300 cuts the registered surface from 51 tools to 15 across slices
S1 to S12. Each slice reads its target from here rather than restating it, so
no slice before S11b re-derives the kernel or bumps ``KERNEL_VERSION``; S11b
makes ``surface_packs.KERNEL_TOOLS`` equal :data:`POST_CUT_KERNEL` and re-pins
the digest once.

Pure: stdlib types only, no ``trw_mcp`` imports, like ``surface_packs``.
"""

from __future__ import annotations

#: Always registered after the cut. ``trw_code`` has been registered since S10,
#: which landed after the code index was bounded (FR15).
POST_CUT_KERNEL: tuple[str, ...] = (
    "trw_session_start",
    "trw_init",
    "trw_status",
    "trw_recall",
    "trw_learn",
    "trw_checkpoint",
    "trw_deliver",
    "trw_build_check",
    "trw_review",
    "trw_prd_validate",
    "trw_code",
)

#: Registered only while the named config flag is true (its default in parentheses):
#: assess_enabled (false), comms_enabled (true), dispatch_tools_exposed (false).
POST_CUT_FLAGGED: dict[str, str] = {
    "trw_assess": "assess_enabled",
    "trw_send": "comms_enabled",
    "trw_inbox": "comms_enabled",
    "trw_dispatch": "dispatch_tools_exposed",
}

#: Every tool that exists after the cut: 15 distinct, 13 registered by default.
POST_CUT_SURFACE: frozenset[str] = frozenset({*POST_CUT_KERNEL, *POST_CUT_FLAGGED})

#: The tool names that mean "a delivery completed" in recorded ``tool_call`` telemetry: the retired reflect tool was folded
#: into ``trw_deliver`` in 7.0, and events recorded before that still carry it. Classifiers of historical events
#: read this one set rather than each spelling the retired name.
DELIVER_COMPLETION_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "trw_deliver",
        "trw_reflect",
    }
)

#: Tools retired before the pre-cut registry of 51 was taken (PRD-CORE-300-FR01, S0), so none is one of the 37 the
#: cut places.
RETIRED_BEFORE_CUT: frozenset[str] = frozenset(
    {
        "trw_knowledge_sync",
        "trw_meta_tune",
        "trw_reflect",
    }
)

#: The 37 tools the cut retires: the pre-cut registry of 51 minus what survived.
CUT_RETIRED_TOOLS: frozenset[str] = frozenset(
    {
        "trw_adopt_run",
        "trw_agent_work_evidence",
        "trw_before_edit_hint",
        "trw_before_edit_hint_batch",
        "trw_channel_stats",
        "trw_claude_md_sync",
        "trw_code_index_update",
        "trw_code_search",
        "trw_code_symbol",
        "trw_codebase_risk_report",
        "trw_cross_repo_ordering",
        "trw_delivery_recover",
        "trw_delivery_status",
        "trw_dispatch_status",
        "trw_graph_related",
        "trw_heartbeat",
        "trw_instructions_sync",
        "trw_mcp_security_status",
        "trw_meta_tune_propose",
        "trw_meta_tune_rollback",
        "trw_ordering_compare",
        "trw_peers",
        "trw_pipeline_health",
        "trw_prd_create",
        "trw_prd_diff",
        "trw_pre_compact_checkpoint",
        "trw_probe",
        "trw_probe_budget_status",
        "trw_profile_explain",
        "trw_query_events",
        "trw_replay_outcomes",
        "trw_request_tool_access",
        "trw_skill_discovery",
        "trw_submit_feedback",
        "trw_surface_classify",
        "trw_surface_diff",
        "trw_validate_agent_work_evidence",
    }
)

#: Every tool name TRW has retired. Shipped text must never tell an agent to call one (PRD-CORE-305-NFR01; the
#: census test reads this).
RETIRED_TOOLS: frozenset[str] = CUT_RETIRED_TOOLS | RETIRED_BEFORE_CUT

#: Tools the cut adds: none of them was in the pre-cut registry of 51.
POST_CUT_NEW_TOOLS: frozenset[str] = frozenset({"trw_code"})

#: The reviewer bound after S11b (NFR02); the per-slice steps are in the PRD.
POST_CUT_REVIEWER_TOOLS: frozenset[str] = frozenset({"trw_recall", "trw_code"})

__all__ = [
    "CUT_RETIRED_TOOLS",
    "POST_CUT_FLAGGED",
    "POST_CUT_KERNEL",
    "POST_CUT_NEW_TOOLS",
    "POST_CUT_REVIEWER_TOOLS",
    "POST_CUT_SURFACE",
    "RETIRED_BEFORE_CUT",
    "RETIRED_TOOLS",
]
