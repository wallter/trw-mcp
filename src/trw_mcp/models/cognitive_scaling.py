"""Pydantic v2 models for Planning-Phase Cognitive Scaling (PRD-SCALE-001).

Sprint-97 scope (Phase 0 + Phase 1): the Scout taxonomy + signal schema +
classification result + the session-profile overlay payload. The drafting /
rubric / dissent-ledger / synthesizer schemas (FR04-FR10) are Sprint-98 and
are intentionally NOT modeled here.

``PlanningMode`` (FR02) is the closed taxonomy. The numeric ``IntEnum`` codes
are stable for telemetry and MUST match the probe-budget table keyed on the
member *name* in :mod:`trw_mcp.probe.budget` (``PLANNING_MODE_BUDGETS``):
``DIRECT=0, DUAL_DRAFT=1, TRIANGULATED=2, TRIANGULATED_WITH_PROBE=3``.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: Ceremony tier the session-layer overlay can request (FR03). Matches the
#: H2 ``Profile.ceremony_tier`` Literal in ``trw_mcp.profile.model`` so the
#: value round-trips through ``session_profile.yaml`` into the resolver.
CeremonyTier = Literal["MINIMAL", "STANDARD", "COMPREHENSIVE"]

#: Precedent-gap is a coarse ordinal: NONE (prior work found) < PARTIAL <
#: HIGH (no precedent). Kept a closed Literal so Scout output is auditable.
PrecedentGap = Literal["NONE", "PARTIAL", "HIGH"]


class PlanningMode(IntEnum):
    """Closed planning-mode taxonomy (FR02).

    Numeric codes are stable for telemetry. The *name* of each member is the
    canonical string written to ``session_profile.yaml`` and used as the
    probe-budget table key in :mod:`trw_mcp.probe.budget`.
    """

    DIRECT = 0
    DUAL_DRAFT = 1
    TRIANGULATED = 2
    TRIANGULATED_WITH_PROBE = 3


#: Ceremony tier each planning mode maps to when Scout writes the session
#: overlay (FR03). DIRECT downgrades to MINIMAL (US-001); escalated modes
#: ride STANDARD/COMPREHENSIVE. This is the dynamic-ceremony lever.
CEREMONY_TIER_BY_MODE: dict[PlanningMode, CeremonyTier] = {
    PlanningMode.DIRECT: "MINIMAL",
    PlanningMode.DUAL_DRAFT: "STANDARD",
    PlanningMode.TRIANGULATED: "COMPREHENSIVE",
    PlanningMode.TRIANGULATED_WITH_PROBE: "COMPREHENSIVE",
}


class ScoutSignals(BaseModel):
    """The three grounded Scout signals (FR01).

    Each signal carries its raw measurement plus a ``threshold_hit`` boolean.
    ``available`` is False when the signal could not be computed (git/grep/
    recall failure) — FR12 degrades to DIRECT when < 2 signals are available.
    """

    model_config = ConfigDict(extra="forbid")

    # blast_radius: symbol fan-out count via grep over declared symbols.
    blast_radius_count: int = Field(default=0, ge=0)
    blast_radius_hit: bool = False
    blast_radius_available: bool = True

    # churn: commit count + unique-author count over the last 6 months.
    churn_commits: int = Field(default=0, ge=0)
    churn_authors: int = Field(default=0, ge=0)
    churn_hit: bool = False
    churn_available: bool = True

    # precedent_gap: trw_recall overlap (NONE = strong precedent found).
    precedent_gap: PrecedentGap = "NONE"
    precedent_gap_hit: bool = False
    precedent_gap_available: bool = True

    def available_count(self) -> int:
        """Count signals that were computable (FR12 degrade gate)."""
        return sum(
            (
                self.blast_radius_available,
                self.churn_available,
                self.precedent_gap_available,
            )
        )

    def hit_count(self) -> int:
        """Count signals whose threshold was crossed (FR01 mode gate)."""
        return sum(
            (
                self.blast_radius_hit and self.blast_radius_available,
                self.churn_hit and self.churn_available,
                self.precedent_gap_hit and self.precedent_gap_available,
            )
        )


class ScoutClassification(BaseModel):
    """Scout output for a session (FR01).

    ``planning_mode`` is the taxonomy decision; ``ceremony_tier`` is the
    derived overlay tier written to ``session_profile.yaml``; ``probe_budget``
    is sourced from the canonical probe-budget table (FR07 source of truth).
    ``escalation_reason`` / ``downgrade_reason`` make the decision auditable.
    """

    model_config = ConfigDict(extra="forbid")

    planning_mode: PlanningMode = PlanningMode.DIRECT
    signals: ScoutSignals = Field(default_factory=ScoutSignals)
    ceremony_tier: CeremonyTier = "MINIMAL"
    probe_budget: int = Field(default=0, ge=0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    escalation_reason: str | None = None
    downgrade_reason: str | None = None
    degraded: bool = False
    source: Literal["scout", "user_override"] = "scout"
    original_mode: PlanningMode | None = None


class SessionProfileOverlay(BaseModel):
    """The session-layer overlay written to ``session_profile.yaml`` (FR03).

    Shape is the subset of the H2 ``Profile`` surface the Scout owns:
    ``ceremony_tier`` (the dynamic-ceremony lever), ``planning_mode`` (numeric
    code for telemetry join), and ``probe_budget``. ``rationale`` is advisory
    provenance — the H2 resolver strips it before composing (see
    ``profile/session_resolve.py::_session_layer``).
    """

    model_config = ConfigDict(extra="forbid")

    ceremony_tier: CeremonyTier
    planning_mode: int = Field(ge=0, le=3)
    probe_budget: int = Field(ge=0)
    rationale: str | None = None


__all__ = [
    "CEREMONY_TIER_BY_MODE",
    "CeremonyTier",
    "PlanningMode",
    "PrecedentGap",
    "ScoutClassification",
    "ScoutSignals",
    "SessionProfileOverlay",
]
