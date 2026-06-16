"""Shared default constants for TRWConfig and domain sub-configs.

Single source of truth for values that appear in both _main.py (TRWConfig)
and _sub_models.py (domain sub-configs).  Both modules import from here
instead of hardcoding their own copies.
"""

# -- Build / mutation --
DEFAULT_BUILD_CHECK_TIMEOUT_SECS: int = 300
DEFAULT_MUTATION_TIMEOUT_SECS: int = 300

# -- Learning storage --
DEFAULT_LEARNING_MAX_ENTRIES: int = 500
DEFAULT_RECALL_RECEIPT_MAX_ENTRIES: int = 1000
DEFAULT_RECALL_MAX_RESULTS: int = 25

# -- Orchestration --
DEFAULT_PARALLELISM_MAX: int = 10

# -- Scoring --
DEFAULT_SCORING_DEFAULT_DAYS_UNUSED: int = 30

# -- Ceremony adaptation (CORE-084) --
LIGHT_MODE_RECALL_CAP: int = 10

# -- Compact mode limits --
COMPACT_TAGS_CAP: int = 10  # Max tags per learning in compact mode

# -- Surface area defaults (PRD-CORE-125) --
DEFAULT_NUDGE_BUDGET_CHARS: int = 600
DEFAULT_LEARNING_PREVIEW_CHARS: int = 500

# -- Tool exposure groups (single source of truth) --
TOOL_GROUP_CORE: tuple[str, ...] = (
    "trw_session_start",
    "trw_checkpoint",
    "trw_learn",
    "trw_deliver",
)
TOOL_GROUP_MEMORY: tuple[str, ...] = (
    "trw_recall",
    "trw_learn_update",
)
TOOL_GROUP_QUALITY: tuple[str, ...] = (
    "trw_build_check",
    "trw_review",
    "trw_prd_create",
    "trw_prd_validate",
)
TOOL_GROUP_OBSERVABILITY: tuple[str, ...] = (
    "trw_status",
    # PRD-HPO-MEAS-001 FR-7 + FR-8: unified event query + surface diff
    "trw_query_events",
    "trw_surface_diff",
    "trw_surface_classify",
    # PRD-INFRA-SEC-001 FR-5: operator status tool for MCP security layer
    "trw_mcp_security_status",
    # PRD-HPO-PROF-001 FR-11: read-only profile-resolution introspection.
    # Bridged so the phase-exposure / first-party bridge over real transport
    # can reach the tool the PROF-001 surface advertises (round-2 e2e F1).
    "trw_profile_explain",
    # PRD-FIX-COMPOUNDING-6 FR02: read-only intelligence-pipeline health probe.
    "trw_pipeline_health",
)
#: PRD-INTENT-002 FR06 phase-exposure override. The phase-mask denial message
#: literally recommends ``trw_request_tool_access`` as the remediation, so it
#: MUST be reachable through the same preset+allowlist bridge that exposes the
#: masked tools — otherwise the first-party bridge denies the very tool the
#: error told the caller to use (round-2 transport e2e F1/F3).
TOOL_GROUP_PHASE_CONTROL: tuple[str, ...] = ("trw_request_tool_access",)
#: Code-intelligence + risk surfaces (read-only / advisory). These are part of
#: the normal agent loop (search, symbol lookup, pre-edit risk, ordering) and
#: were registered but never bridged, so direct calls were denied with
#: ``tool_not_in_server_capabilities`` over real transport (round-2 e2e F3).
TOOL_GROUP_CODE_INTEL: tuple[str, ...] = (
    # PRD-CORE-170 read-only skill meta-discovery.
    "trw_skill_discovery",
    # Local code search / symbol lookup + SHA-256 index refresh.
    "trw_code_search",
    "trw_code_symbol",
    "trw_code_index_update",
    # PRD-DIST-1983/1984/1989 pre-edit risk hints (single + batch).
    "trw_before_edit_hint",
    "trw_before_edit_hint_batch",
    # PRD-DIST-1990 / PRD-CORE-167 codebase + entity risk reporting.
    "trw_codebase_risk_report",
    "trw_entity_risk_map",
    # PRD-DIST-1994/1995 build-ordering comparison + cross-repo ordering.
    "trw_ordering_compare",
    "trw_cross_repo_ordering",
)
#: Agent-work-evidence + PRD-diff surfaces consumed during normal coordination.
TOOL_GROUP_EVIDENCE: tuple[str, ...] = (
    # AgentWorkEvidence v1 export + validation (delivered=wired coordination).
    "trw_agent_work_evidence",
    "trw_validate_agent_work_evidence",
    # PRD-diff structural comparison (read-only).
    "trw_prd_diff",
    # Backend feedback submission portal (thin client; user-initiated).
    "trw_submit_feedback",
)
#: PRD-CORE-144 empirical-probe tools. Kept as their own group so a named
#: preset that opts into probing does not silently lose the tools (F-12). The
#: tools are registered unconditionally; ``trw_probe`` is additionally gated at
#: the tool layer by ``TRW_PROBE_ENABLED`` (default OFF, §9 Phase 1).
TOOL_GROUP_PROBE: tuple[str, ...] = (
    "trw_probe",
    "trw_probe_budget_status",
)
TOOL_GROUP_ADMIN: tuple[str, ...] = (
    "trw_pre_compact_checkpoint",
    "trw_init",
    "trw_instructions_sync",
    # Deprecated alias for trw_instructions_sync; retained for backward compat.
    "trw_claude_md_sync",
    # trw_knowledge_sync removed by PRD-FIX-076 (dead surface; internal
    # knowledge-sync logic still fires during deliver/backfill).
    # PRD-CORE-141 FR07/FR08 — per-connection pin liveness + adoption.
    "trw_heartbeat",
    "trw_adopt_run",
    "trw_meta_tune_rollback",
)

#: Tools that are REGISTERED on the production server but DELIBERATELY excluded
#: from every preset (and therefore from the first-party allowlist bridge in
#: ``middleware/_mcp_security_helpers.first_party_tool_scope``). Each exclusion
#: is a security/operator-surface judgment, not an oversight. The bridge-parity
#: regression test (``test_tool_preset_bridge_parity``) asserts that every
#: registered tool is either in ``TOOL_PRESETS["all"]`` or named here, so any
#: NEW registered tool that is silently unbridged fails CI (round-2 e2e F3).
INTENTIONALLY_UNBRIDGED_TOOLS: frozenset[str] = frozenset(
    {
        # SAFE-001 self-modification: proposes promotions of advisory edits.
        # This mutates the framework's own surfaces and is an operator gate, not
        # part of the agent work loop. Its inverse, trw_meta_tune_rollback, is a
        # safety operation and IS bridged (TOOL_GROUP_ADMIN).
        "trw_meta_tune_propose",
        # Nudge-channel internals (PRD-DIST-2400): render + correlation/throttle
        # health are operator/telemetry surfaces driven by the framework itself,
        # not tools an agent calls during a coding session.
        "trw_channel_render",
        "trw_channel_stats",
    }
)

TOOL_PRESETS: dict[str, tuple[str, ...]] = {
    "core": TOOL_GROUP_CORE,
    "minimal": TOOL_GROUP_CORE + TOOL_GROUP_MEMORY,
    "standard": TOOL_GROUP_CORE + TOOL_GROUP_MEMORY + TOOL_GROUP_QUALITY + ("trw_status", "trw_init"),
    "all": (
        TOOL_GROUP_CORE
        + TOOL_GROUP_MEMORY
        + TOOL_GROUP_QUALITY
        + TOOL_GROUP_OBSERVABILITY
        + TOOL_GROUP_ADMIN
        + TOOL_GROUP_PROBE
        + TOOL_GROUP_PHASE_CONTROL
        + TOOL_GROUP_CODE_INTEL
        + TOOL_GROUP_EVIDENCE
    ),
}
