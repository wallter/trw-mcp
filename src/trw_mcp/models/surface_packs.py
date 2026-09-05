"""Single source of truth for the TRW capability-pack membership (PRD-CORE-218).

This module is deliberately *pure* — stdlib types only, zero ``trw_mcp`` imports —
so every consumer can read it without an import cycle or a side effect:

* ``server/_surface_manifest_registry.py`` builds the authoritative
  ``SurfaceManifestEntry`` manifest + kernel digest pin from ``PACK_TOOLS`` /
  ``KERNEL_TOOLS`` / ``STANDARD_TASK_PACKS`` here (FR01/FR02/FR04);
* ``models/config/_defaults.py`` re-exports ``KERNEL_TOOLS`` /
  ``CAPABILITY_PACKS`` (the non-kernel view) / ``STANDARD_TASK_PACKS`` so
  ``models/config/_profiles.py`` resolves FR03 packs against the *same* data.

Before this module the pack membership was duplicated in two divergent tables
(``_defaults`` and the registry). They are now derived from this one table, so
``run_maintenance`` / ``experimentation`` / ``telemetry_security`` /
``memory_management`` can never silently disagree again. Membership is a
12-pack / 50-tool inventory: the 45 tools enumerated in PRD-CORE-218 §4
("Exact Initial Capability Packs") plus the 5 additional live-registered tools
folded in so the FR01 manifest stays a bijection with the registrar (every
registered tool belongs to exactly one pack). Every tool ID is a live
registered tool.
"""

from __future__ import annotations

#: The EXACT nine-tool universal kernel (FR02). Every profile resolution
#: includes exactly these and no other tool is kernel; a membership change is a
#: versioned event (registry digest pin enforces the version bump).
KERNEL_TOOLS: tuple[str, ...] = (
    "trw_session_start",
    "trw_status",
    "trw_recall",
    "trw_learn",
    "trw_checkpoint",
    "trw_deliver",
    "trw_skill_discovery",
    "trw_request_tool_access",
    "trw_profile_explain",
)

#: The eleven non-kernel capability packs -> exact ordered tool IDs. This is the
#: complete registered surface (operator-only tools such as
#: ``trw_meta_tune_propose`` / ``trw_channel_stats`` /
#: ``trw_replay_outcomes`` each belong to exactly one pack so the FR01 manifest
#: stays a bijection with the registrar). Public status / lifecycle are decided
#: by the registry, not here — this table is membership only.
CAPABILITY_PACKS: dict[str, tuple[str, ...]] = {
    "verification": ("trw_build_check", "trw_review"),
    "requirements": ("trw_prd_create", "trw_prd_diff", "trw_prd_validate"),
    "code_navigation": (
        "trw_code_search",
        "trw_code_symbol",
        "trw_before_edit_hint",
        "trw_before_edit_hint_batch",
    ),
    "code_risk": (
        "trw_code_index_update",
        "trw_codebase_risk_report",
        "trw_ordering_compare",
        "trw_cross_repo_ordering",
    ),
    "delivery_operations": ("trw_delivery_status", "trw_delivery_recover"),
    "run_maintenance": (
        "trw_heartbeat",
        "trw_pre_compact_checkpoint",
        "trw_adopt_run",
        "trw_init",
        "trw_instructions_sync",
        "trw_claude_md_sync",
        "trw_replay_outcomes",
    ),
    "dispatch": (
        "trw_dispatch",
        "trw_dispatch_status",
        "trw_agent_work_evidence",
        "trw_validate_agent_work_evidence",
    ),
    "experimentation": (
        "trw_probe",
        "trw_probe_budget_status",
        "trw_meta_tune_rollback",
        "trw_meta_tune_propose",
    ),
    "telemetry_security": (
        "trw_query_events",
        "trw_surface_classify",
        "trw_surface_diff",
        "trw_mcp_security_status",
        "trw_pipeline_health",
        "trw_channel_stats",
    ),
    "memory_management": ("trw_learn_update", "trw_graph_related"),
    "feedback": ("trw_submit_feedback",),
}

#: pack -> tool IDs including the kernel modelled as a pack, so the manifest is
#: uniform (every registered tool belongs to exactly one pack). Derived — never
#: hand-maintained — so it can never diverge from ``KERNEL_TOOLS`` /
#: ``CAPABILITY_PACKS``.
PACK_TOOLS: dict[str, tuple[str, ...]] = {"kernel": KERNEL_TOOLS, **CAPABILITY_PACKS}

#: Registered operator-only tools. They remain in ``PACK_TOOLS`` so registrar
#: parity is complete, but are excluded from the eligible public agent surface.
#: Kept in this pure module so inventory generation and runtime admission read
#: the same authority without importing/booting the MCP server.
OPERATOR_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "trw_meta_tune_propose",
        "trw_channel_stats",
        "trw_replay_outcomes",
    }
)

#: The read-only REVIEWER surface (PRD-SEC-015-FR01) — the single source of
#: truth for what a dispatched second-opinion lane may call. Nine read-report
#: tools, each already a registered id drawn from :data:`KERNEL_TOOLS` and the
#: ``code_navigation`` / ``memory_management`` / ``code_risk`` packs, so pack
#: membership is UNCHANGED and no versioned kernel event occurs.
#:
#: This set REPLACES the resolved surface for a reviewer session; it is never
#: subtracted from the never-hide union. That is load-bearing: the never-hide
#: set grew on 2026-09-04 when ``trw_prd_validate`` joined the middleware's
#: bootstrap set, and a subtractive design would silently re-widen every
#: reviewer surface on the next such addition.
#:
#: Excluded, with the reason that settles each class:
#:   * ``trw_session_start`` / ``trw_learn`` / ``trw_learn_update`` /
#:     ``trw_checkpoint`` / ``trw_init`` / ``trw_adopt_run`` — write the shared
#:     learnings store or run state a stateless reviewer does not own (the
#:     measured pollution path: 24 ``trw_deliver`` + 12 ``trw_build_check`` +
#:     12 ``trw_learn`` across 11 runs, 2026-08-27);
#:   * ``trw_build_check`` / ``trw_review`` / ``trw_deliver`` — verdict and gate
#:     machinery; a lane that ran no build must not fabricate its evidence;
#:   * ``trw_request_tool_access`` — self-escalation; the bound must not be
#:     reachable from inside the bounded lane;
#:   * the ``dispatch`` pack — recursion (a reviewer spawning reviewers);
#:   * ``trw_instructions_sync`` / ``trw_claude_md_sync`` — they rewrite
#:     hand-written operating rules (128 lines / 9 sections truncated
#:     2026-07-23);
#:   * ``trw_status`` — an unpinned child resolves through ``find_active_run``
#:     to ANOTHER agent's run and increments a persisted ceremony counter;
#:   * ``trw_prd_validate`` — read-only in name only: it advances the ACTIVE
#:     run's phase to PLAN, so in an unpinned child it is a cross-session write.
REVIEWER_TOOLS: frozenset[str] = frozenset(
    {
        "trw_recall",
        "trw_code_search",
        "trw_code_symbol",
        "trw_before_edit_hint",
        "trw_before_edit_hint_batch",
        "trw_graph_related",
        "trw_skill_discovery",
        "trw_profile_explain",
        "trw_codebase_risk_report",
    }
)


def reviewer_tools_toml_array() -> str:
    """Render :data:`REVIEWER_TOOLS` as a TOML array literal (PRD-SEC-015-FR01).

    The ONE rendering of the reviewer set for the Codex ``-c
    mcp_servers.trw.enabled_tools=[...]`` allowlist layer — emitted by the
    dispatch call site and by ``scripts/print_reviewer_tools.py`` so the two
    layers can never disagree the way the generated client config and the server
    surface do. Tool ids are ``[a-z_]`` only, so plain double quotes are exact
    TOML; the output round-trips through ``tomllib`` to ``sorted(REVIEWER_TOOLS)``.
    """
    return "[" + ", ".join(f'"{name}"' for name in sorted(REVIEWER_TOOLS)) + "]"


#: Standard task-to-pack mapping (versioned manifest fixture). ``kernel`` is
#: always implied. Keys are aligned to the PRODUCTION ``TaskType`` vocabulary
#: (``models/task_profile_types.TaskType`` + ``tools/_task_type_detection``:
#: coding | research | docs | eval | rca | planning | unknown) — round-1 audit F2:
#: the prior ``audit`` key was UNREACHABLE (not a TaskType; an explicit
#: task_type="audit" falls to ``unknown``) and research/eval/rca/planning
#: silently resolved kernel-only. Pack names are derived only from
#: :data:`PACK_TOOLS` — no packs are invented. Resolved counts (kernel=9):
#:   coding   = kernel + verification(2) + code_navigation(4)        = 15
#:   research = kernel + code_navigation(4) + memory_management(2)   = 15
#:   docs     = kernel + requirements(3) + verification(2)           = 14
#:   eval     = kernel + verification(2)                             = 11
#:   rca      = kernel + code_navigation(4) + verification(2)        = 15
#:   planning = kernel + requirements(3)                             = 12
#:   unknown  = kernel + verification(2)                             = 11
#: PRD-CORE-246-FR05: ``unknown`` declares the ``verification`` pack and is the
#: FALLBACK for an unresolvable (``None``/empty) or unmapped task type, replacing
#: the previous ``()`` / kernel-only resolution. The runtime already exposed both
#: verification tools through the middleware's never-hide union, so this removes
#: a divergence between the declared authority and the runtime rather than
#: widening exposure — and it means every consumer that reads THIS table alone
#: (e.g. ``TRWConfig.resolve_tool_surface_for_task``) now agrees with the server.
#: TOMBSTONE (F2): the ``audit`` key was removed — audit-like work has no
#: distinct ``TaskType`` producer; audit runs classify as ``coding``/``rca`` and
#: get the same navigation+verification surface. Since FR05 an explicit
#: ``task_type="audit"`` resolves to the ``unknown`` fallback (kernel +
#: verification), not silently to kernel only. Re-add only with a real
#: ``TaskType`` + detector producer.
#: OQ-002 (PRD-CORE-246): ``code_risk``, ``delivery_operations``, ``dispatch``,
#: ``experimentation``, ``feedback``, ``run_maintenance`` and
#: ``telemetry_security`` are named by NO task type. That is deliberate, not a
#: second tombstone — they are reachable through ``trw_request_tool_access``
#: (and, for ``trw_init``/``trw_submit_feedback``, through the middleware's
#: bootstrap never-hide set).
STANDARD_TASK_PACKS: dict[str, tuple[str, ...]] = {
    "coding": ("verification", "code_navigation"),
    "research": ("code_navigation", "memory_management"),
    "docs": ("requirements", "verification"),
    "eval": ("verification",),
    "rca": ("code_navigation", "verification"),
    "planning": ("requirements",),
    "unknown": ("verification",),
}

__all__ = [
    "CAPABILITY_PACKS",
    "KERNEL_TOOLS",
    "OPERATOR_ONLY_TOOLS",
    "PACK_TOOLS",
    "REVIEWER_TOOLS",
    "STANDARD_TASK_PACKS",
    "reviewer_tools_toml_array",
]
