"""Adaptive ceremony depth: complexity classification and tier-aware scoring.

PRD-CORE-060: Complexity signals -> tier -> phase requirements -> ceremony score.

Internal module -- all public names are re-exported from ``trw_mcp.scoring``.
"""

from __future__ import annotations

from dataclasses import dataclass

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.run import (
    ComplexityClass,
    ComplexityOverride,
    ComplexitySignals,
    PhaseRequirements,
)

# Tier-aware ceremony scoring lives in the sibling ``_tier_score`` Module.
# Re-exported here so the historical ``trw_mcp.scoring._complexity`` import
# path keeps working alongside the ``trw_mcp.scoring`` facade.
from trw_mcp.scoring._tier_score import (
    _TIER_EXPECTATIONS as _TIER_EXPECTATIONS,
)
from trw_mcp.scoring._tier_score import (
    _TierExpectation as _TierExpectation,
)
from trw_mcp.scoring._tier_score import (
    compute_tier_ceremony_score as compute_tier_ceremony_score,
)
from trw_mcp.scoring._utils import get_config

# --- Adaptive Ceremony Depth (PRD-CORE-060) ---

# High-risk signal names used for hard override detection (FR05)
_HIGH_RISK_SIGNALS: tuple[str, ...] = (
    "security_change",
    "data_migration",
    "unknown_codebase",
)


def classify_complexity(
    signals: ComplexitySignals,
    config: TRWConfig | None = None,
) -> tuple[ComplexityClass, int, ComplexityOverride | None]:
    """Classify task complexity into MINIMAL/STANDARD/COMPREHENSIVE (FR01+FR05).

    Computes a raw score using the 6-signal point formula, then applies
    hard overrides for high-risk signals.

    Args:
        signals: The 6+3 complexity signals from the caller.
        config: Optional config override; uses singleton if None.

    Returns:
        Tuple of (tier, raw_score, override_or_None).
    """
    cfg = config or get_config()

    # FR01: Compute raw score from 6 core signals
    files_capped = min(signals.files_affected, cfg.complexity_weight_files_affected_max)
    raw_score = (
        files_capped
        + (cfg.complexity_weight_novel_patterns if signals.novel_patterns else 0)
        + (cfg.complexity_weight_cross_cutting if signals.cross_cutting else 0)
        + (cfg.complexity_weight_architecture_change if signals.architecture_change else 0)
        + (cfg.complexity_weight_external_integration if signals.external_integration else 0)
        + (cfg.complexity_weight_large_refactoring if signals.large_refactoring else 0)
    )

    # FR01: Tier assignment from raw score
    if raw_score <= cfg.complexity_tier_minimal:
        tier = ComplexityClass.MINIMAL
    elif raw_score >= cfg.complexity_tier_comprehensive + 1:
        tier = ComplexityClass.COMPREHENSIVE
    else:
        tier = ComplexityClass.STANDARD

    # FR05: Hard override for high-risk signals
    active_risk_signals = [name for name in _HIGH_RISK_SIGNALS if getattr(signals, name, False)]
    override: ComplexityOverride | None = None

    if len(active_risk_signals) >= cfg.complexity_hard_override_threshold:
        # 2+ high-risk signals -> force COMPREHENSIVE
        override = ComplexityOverride(
            reason="hard override: multiple high-risk signals",
            signals=active_risk_signals,
            raw_score=raw_score,
        )
        tier = ComplexityClass.COMPREHENSIVE
    elif len(active_risk_signals) == 1 and tier == ComplexityClass.MINIMAL:
        # Single high-risk signal escalates MINIMAL -> STANDARD
        override = ComplexityOverride(
            reason="escalation: single high-risk signal prevents MINIMAL",
            signals=active_risk_signals,
            raw_score=raw_score,
        )
        tier = ComplexityClass.STANDARD

    return tier, raw_score, override


def get_phase_requirements(tier: ComplexityClass) -> PhaseRequirements:
    """Return phase mandatory/optional/skipped classification for a tier (FR04).

    IMPLEMENT and DELIVER are never in skipped regardless of tier.

    Args:
        tier: The complexity tier.

    Returns:
        PhaseRequirements model.
    """
    if tier == ComplexityClass.MINIMAL:
        # MINIMAL is for truly trivial tasks (1 file, typo fix).
        # Still requires IMPLEMENT + VALIDATE + DELIVER — skipping tests is never OK.
        return PhaseRequirements(
            mandatory=["IMPLEMENT", "VALIDATE", "DELIVER"],
            optional=[],
            skipped=["RESEARCH", "PLAN", "REVIEW"],
        )
    if tier == ComplexityClass.STANDARD:
        # STANDARD is the default for most tasks. REVIEW is mandatory —
        # independent verification before delivery prevents false completion.
        return PhaseRequirements(
            mandatory=["PLAN", "IMPLEMENT", "VALIDATE", "REVIEW", "DELIVER"],
            optional=[],
            skipped=["RESEARCH"],
        )
    # COMPREHENSIVE — all phases mandatory
    return PhaseRequirements(
        mandatory=["RESEARCH", "PLAN", "IMPLEMENT", "VALIDATE", "REVIEW", "DELIVER"],
        optional=[],
        skipped=[],
    )


@dataclass(frozen=True, slots=True)
class CeremonyDepthContract:
    """Canonical phase/trace/nudge contract for a complexity tier."""

    tier: ComplexityClass
    ceremony_depth: str
    mandatory_phases: tuple[str, ...]
    nudge_policy: str
    trace_depth: str
    validation_required: bool = True


def get_ceremony_depth_contract(tier: ComplexityClass) -> CeremonyDepthContract:
    """Return the normalized ceremony-depth contract for a tier.

    This keeps public ``ceremony_mode`` values (``full``/``light``) separate
    from task-level depth resolution. Even MINIMAL tasks still require
    validation; light means fewer prompts and a shallower trace, not no tests.
    """
    requirements = get_phase_requirements(tier)
    if tier == ComplexityClass.MINIMAL:
        return CeremonyDepthContract(
            tier=tier,
            ceremony_depth="light",
            mandatory_phases=tuple(requirements.mandatory),
            nudge_policy="sparse",
            trace_depth="minimal",
        )
    if tier == ComplexityClass.COMPREHENSIVE:
        return CeremonyDepthContract(
            tier=tier,
            ceremony_depth="comprehensive",
            mandatory_phases=tuple(requirements.mandatory),
            nudge_policy="dense",
            trace_depth="causal",
        )
    return CeremonyDepthContract(
        tier=tier,
        ceremony_depth="standard",
        mandatory_phases=tuple(requirements.mandatory),
        nudge_policy="standard",
        trace_depth="standard",
    )


__all__ = [
    "_HIGH_RISK_SIGNALS",
    "_TIER_EXPECTATIONS",
    "_TierExpectation",
    "classify_complexity",
    "compute_tier_ceremony_score",
    "get_ceremony_depth_contract",
    "get_phase_requirements",
]
