"""Per-phase tool policy — PRD-INTENT-002 FR01.

``PhaseToolPolicy`` validates and normalizes the
``ResolvedProfile.allowed_tools_by_phase`` mapping (PROF-001's policy surface)
plus a global ``safe_set`` (the ALL_PHASES bucket of read-only / lifecycle /
help tools). The middleware consumes the RESOLVED profile via
:func:`from_resolved_allowlist` — it never defines a second allowlist table.

``DEFAULT_PHASE_POLICY`` is the defaults-layer seed policy PROF-001 ships. It is
rebuilt from the POST-FIX-076 39-tool surface (none of the four removed tools
— ``trw_ceremony_status``/``trw_ceremony_approve``/``trw_ceremony_revert``/
``trw_knowledge_sync`` — appear in any bucket). ``test_all_tool_names_are_registered``
reads ``build/inventory.json`` at test time so a newly-registered tool forces a
policy update via CI failure.

``RIGID_TOOLS`` (session_start / deliver / build_check) are the never-hidden
invariant: they live in the Safe Set so they are visible in every phase even if
a profile's ``allowed_tools_by_phase`` omits them.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

#: The six canonical phase labels (uppercase — matching ``profile.PhaseName``).
_PHASES: tuple[str, ...] = ("RESEARCH", "PLAN", "IMPLEMENT", "VALIDATE", "REVIEW", "DELIVER")

#: Tools that MUST be exposed in EVERY phase regardless of policy (the
#: never-hide invariant). A broken or over-tight policy can never lock a
#: session out of starting, validating, or delivering.
RIGID_TOOLS: frozenset[str] = frozenset({"trw_session_start", "trw_deliver", "trw_build_check"})

#: The Safe Set (ALL_PHASES): lifecycle, status, recall, help, and read-only
#: code-intelligence tools that are phase-agnostic. Includes the rigid tools.
_DEFAULT_SAFE_SET: frozenset[str] = frozenset(
    {
        # Lifecycle / status / recall / discoveries (phase-agnostic)
        "trw_session_start",
        "trw_status",
        "trw_recall",
        "trw_learn",
        "trw_learn_update",
        "trw_heartbeat",
        "trw_pre_compact_checkpoint",
        "trw_skill_discovery",
        "trw_submit_feedback",
        "trw_mcp_security_status",
        "trw_pipeline_health",
        # Read-only profile / probe-budget introspection (phase-agnostic)
        "trw_profile_explain",
        "trw_probe_budget_status",
        # The phase-exposure override request itself must reach any phase so a
        # masked-out session can always ask for access (PRD-INTENT-002 FR06).
        "trw_request_tool_access",
        # Rigid (also session-lifecycle gates, but listed for VALIDATE/DELIVER)
        "trw_build_check",
        "trw_deliver",
        # Read-only code / risk / event intelligence (safe to read any phase)
        "trw_before_edit_hint",
        "trw_before_edit_hint_batch",
        "trw_code_search",
        "trw_code_symbol",
        "trw_codebase_risk_report",
        "trw_entity_risk_map",
        "trw_query_events",
        "trw_surface_classify",
        "trw_surface_diff",
        "trw_ordering_compare",
        "trw_cross_repo_ordering",
        "trw_agent_work_evidence",
        "trw_validate_agent_work_evidence",
        # Delivery-journal status projection (PRD-CORE-208 FR05) — mechanically
        # read-only (opens the operation store mode=ro, never mutates), so it is
        # phase-agnostic and belongs in the Safe Set.
        "trw_delivery_status",
        # Cross-client dispatch (Phase 3): launch + poll a second-opinion audit
        # by another coding-agent CLI. Read-only by default and phase-agnostic
        # (an agent may want an independent review in any phase), so kept in the
        # Safe Set rather than gated to one phase.
        "trw_dispatch",
        "trw_dispatch_status",
        # Read-only knowledge-graph traversal — phase-agnostic read.
        "trw_graph_related",
    }
)

#: The defaults-layer per-phase action allowlist (excluding Safe Set overlap).
#: This is the seed PROF-001 ships; it is regenerated from the 39-tool surface.
_DEFAULT_BY_PHASE: dict[str, list[str]] = {
    "RESEARCH": ["trw_init", "trw_adopt_run"],
    "PLAN": [
        "trw_adopt_run",
        "trw_prd_create",
        "trw_prd_validate",
        "trw_prd_diff",
        "trw_probe",
    ],
    "IMPLEMENT": ["trw_checkpoint", "trw_code_index_update"],
    "VALIDATE": [
        "trw_checkpoint",
        "trw_meta_tune_rollback",
    ],
    "REVIEW": [
        "trw_checkpoint",
        "trw_review",
        "trw_prd_validate",
        "trw_prd_diff",
        "trw_meta_tune_rollback",
    ],
    "DELIVER": [
        "trw_checkpoint",
        "trw_claude_md_sync",
        "trw_instructions_sync",
        # Capability-guarded recovery of a stale/crashed delivery operation
        # (PRD-CORE-208 FR04) — a delivery-phase mutation, gated to DELIVER.
        "trw_delivery_recover",
    ],
}


def _normalize_phase(phase: str) -> str:
    """Return the uppercase canonical phase label for ``phase`` (any case)."""
    return phase.strip().upper()


class PhaseToolPolicy(BaseModel):
    """Validated per-phase tool allowlist plus a global Safe Set (FR01).

    ``allowed_tools_by_phase`` keys are normalized to the uppercase phase form
    so a profile may declare them in either the ``Phase`` enum value case
    (lowercase) or the ``profile.PhaseName`` case (uppercase).
    """

    model_config = ConfigDict(frozen=True)

    allowed_tools_by_phase: dict[str, list[str]]
    safe_set: frozenset[str]

    @field_validator("allowed_tools_by_phase", mode="before")
    @classmethod
    def _normalize_keys(cls, value: Any) -> Any:
        """Normalize phase keys to uppercase; tolerate non-dict (let pydantic raise)."""
        if not isinstance(value, dict):
            return value
        normalized: dict[str, list[str]] = {}
        for key, tools in value.items():
            normalized[_normalize_phase(str(key))] = list(tools)
        return normalized

    def list_for(self, phase: str) -> frozenset[str]:
        """Return the visible tool set for ``phase`` (phase subset ∪ Safe Set).

        An unknown phase yields the Safe Set alone (never a crash, never empty).
        """
        canonical = _normalize_phase(phase)
        phase_tools = self.allowed_tools_by_phase.get(canonical, [])
        return frozenset(phase_tools) | self.safe_set


#: The defaults-layer seed policy PROF-001 ships (single source of truth seed).
DEFAULT_PHASE_POLICY = PhaseToolPolicy(
    allowed_tools_by_phase={phase: list(_DEFAULT_BY_PHASE[phase]) for phase in _PHASES},
    safe_set=_DEFAULT_SAFE_SET,
)


def from_resolved_allowlist(
    allowed_tools_by_phase: dict[str, list[str]] | None,
    *,
    safe_set: frozenset[str] | None = None,
) -> PhaseToolPolicy:
    """Build a policy from a RESOLVED profile's ``allowed_tools_by_phase``.

    This is the SINGLE-SOURCE consumption path (PROF-001 FR-14): the middleware
    passes ``ResolvedProfile.profile.allowed_tools_by_phase`` and gets a policy
    back. A ``None``/empty mapping falls back to :data:`DEFAULT_PHASE_POLICY`'s
    buckets so a profile that omits the field still gets sane phase masking.

    The Safe Set is always unioned with :data:`RIGID_TOOLS` so the never-hide
    invariant holds even when a profile supplies a minimal safe set.
    """
    effective_safe = (safe_set or DEFAULT_PHASE_POLICY.safe_set) | RIGID_TOOLS
    if not allowed_tools_by_phase:
        return PhaseToolPolicy(
            allowed_tools_by_phase={
                phase: list(tools) for phase, tools in DEFAULT_PHASE_POLICY.allowed_tools_by_phase.items()
            },
            safe_set=effective_safe,
        )
    return PhaseToolPolicy(
        allowed_tools_by_phase=allowed_tools_by_phase,
        safe_set=effective_safe,
    )


__all__ = [
    "DEFAULT_PHASE_POLICY",
    "RIGID_TOOLS",
    "PhaseToolPolicy",
    "from_resolved_allowlist",
]
