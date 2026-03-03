"""Risk-based validation scaling (PRD-QUAL-013).

Risk profiles adjust quality tier thresholds, content density minimums,
and dimension weight distributions based on PRD priority or explicit risk.
"""

from __future__ import annotations

from dataclasses import dataclass

from trw_mcp.models.config import TRWConfig


@dataclass(frozen=True, slots=True)
class RiskProfile:
    """Risk-scaled thresholds and dimension weights for PRD validation.

    Each risk level gets a distinct profile that adjusts quality tier
    thresholds, content density minimums, and dimension weight distribution.
    """

    approved_threshold: float
    review_threshold: float
    draft_threshold: float
    min_content_density: float
    weights: tuple[float, ...]  # (density, structure, trace, smell, read, ears)


RISK_PROFILES: dict[str, RiskProfile] = {
    "critical": RiskProfile(92.0, 75.0, 45.0, 0.50, (20, 15, 25, 20, 5, 15)),
    "high":     RiskProfile(88.0, 70.0, 35.0, 0.40, (22, 15, 23, 18, 7, 15)),
    "medium":   RiskProfile(85.0, 60.0, 30.0, 0.30, (25, 15, 20, 15, 10, 15)),
    "low":      RiskProfile(75.0, 50.0, 20.0, 0.20, (30, 15, 15, 10, 15, 15)),
}

_PRIORITY_TO_RISK: dict[str, str] = {
    "P0": "critical",
    "P1": "high",
    "P2": "medium",
    "P3": "low",
}


def derive_risk_level(priority: str, explicit_risk: str | None = None) -> str:
    """Derive risk level from priority or explicit override.

    Args:
        priority: PRD priority (P0, P1, P2, P3).
        explicit_risk: Explicit risk level override. Takes precedence.

    Returns:
        Risk level string: critical, high, medium, or low.
    """
    if explicit_risk and explicit_risk in RISK_PROFILES:
        return explicit_risk
    return _PRIORITY_TO_RISK.get(priority, "medium")


def get_risk_scaled_config(config: TRWConfig, risk_level: str) -> TRWConfig:
    """Return a config copy with risk-scaled thresholds and weights.

    Uses ``model_copy(update=...)`` — never mutates the original config.
    Returns the original config unchanged if risk scaling is disabled
    or if risk_level is "medium" (baseline).

    Args:
        config: Original TRWConfig.
        risk_level: Risk level to scale to.

    Returns:
        TRWConfig with adjusted thresholds/weights, or original if no scaling needed.
    """
    if not config.risk_scaling_enabled or risk_level == "medium":
        return config

    profile = RISK_PROFILES.get(risk_level)
    if profile is None:
        return config

    weights = profile.weights
    return config.model_copy(update={
        # Tier thresholds (names in config are offset by one tier — historical)
        "validation_review_threshold": profile.approved_threshold,
        "validation_draft_threshold": profile.review_threshold,
        "validation_skeleton_threshold": profile.draft_threshold,
        # Content density minimum
        "prd_min_content_density": profile.min_content_density,
        # Dimension weights
        "validation_density_weight": weights[0],
        "validation_structure_weight": weights[1],
        "validation_traceability_weight": weights[2],
        "validation_smell_weight": weights[3],
        "validation_readability_weight": weights[4],
        "validation_ears_weight": weights[5],
    })
