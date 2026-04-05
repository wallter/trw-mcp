"""Inverse Propensity Scoring weighted estimation.

PRD-CORE-108-FR01 Tier 1: When propensity-logged exploration data
provides 10+ observations per learning, use IPS-weighted estimation.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from trw_mcp.scoring.attribution._common import map_estimate_to_category

logger = structlog.get_logger(__name__)

# Minimum propensity to prevent extreme weights
_PROPENSITY_FLOOR: float = 0.05

# Minimum observations required for IPS estimation
_MIN_OBSERVATIONS: int = 10


@dataclass(frozen=True)
class AttributionResult:
    """Result of IPS or DML attribution for a single learning."""

    outcome_correlation: str
    estimate: float
    tier: str
    observations: int
    client_profile: str = ""
    model_family: str = ""


# Backward-compatible alias for any external callers
_map_estimate_to_category = map_estimate_to_category


def compute_ips_attribution(
    learning_id: str,
    propensity_records: list[dict[str, object]],
    outcomes: list[dict[str, object]],
    client_profile: str = "",
    model_family: str = "",
) -> AttributionResult:
    """Compute IPS-weighted outcome attribution for a single learning.

    Uses Inverse Propensity Scoring to estimate the causal effect of
    surfacing a learning on session outcomes.

    Args:
        learning_id: ID of the learning being attributed.
        propensity_records: List of dicts with ``selection_probability``
            and ``exploration`` fields.
        outcomes: List of dicts with ``value`` field (numeric outcome).
        client_profile: IDE/client that created the attribution context.
        model_family: AI model family for stratification.

    Returns:
        AttributionResult with outcome_correlation, estimate, tier, and
        observation count.
    """
    n = min(len(propensity_records), len(outcomes))

    if n < _MIN_OBSERVATIONS:
        logger.debug(
            "ips_insufficient_observations",
            learning_id=learning_id,
            observations=n,
            required=_MIN_OBSERVATIONS,
        )
        return AttributionResult(
            outcome_correlation="insufficient_data",
            estimate=0.0,
            tier="ips",
            observations=n,
            client_profile=client_profile,
            model_family=model_family,
        )

    # Compute IPS-weighted estimate: sum(outcome_i / max(propensity_i, floor)) / n
    weighted_sum = 0.0
    for i in range(n):
        outcome_val = float(str(outcomes[i].get("value", 0.0)))
        propensity = float(str(propensity_records[i].get("selection_probability", 1.0)))
        clamped_propensity = max(propensity, _PROPENSITY_FLOOR)
        weighted_sum += outcome_val / clamped_propensity

    estimate = weighted_sum / n
    category = _map_estimate_to_category(estimate)

    logger.info(
        "ips_attribution_computed",
        learning_id=learning_id,
        observations=n,
        estimate=round(estimate, 4),
        outcome_correlation=category,
    )

    return AttributionResult(
        outcome_correlation=category,
        estimate=estimate,
        tier="ips",
        observations=n,
        client_profile=client_profile,
        model_family=model_family,
    )
