"""ERM-style selective attribution for co-surfaced learnings.

PRD-CORE-108-FR02: Distributes credit proportionally among co-surfaced
learnings using softmax normalization over domain match scores.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class CreditShare:
    """Credit allocation for a single co-surfaced learning."""

    learning_id: str
    share: float
    relevance_score: float


def _compute_relevance(surface: dict[str, object]) -> float:
    """Compute relevance score from domain_match and temporal_proximity.

    Formula: domain_match * 0.7 + temporal_proximity * 0.3
    """
    domain_match = float(str(surface.get("domain_match", 0.0)))
    temporal_proximity = float(str(surface.get("temporal_proximity", 0.0)))
    return domain_match * 0.7 + temporal_proximity * 0.3


def _softmax(scores: list[float], temperature: float) -> list[float]:
    """Apply softmax normalization with temperature scaling.

    Temperature controls distribution sharpness:
    - Lower temperature -> sharper (winner-take-all)
    - Higher temperature -> more uniform

    Args:
        scores: Raw relevance scores.
        temperature: Softmax temperature parameter (must be > 0).

    Returns:
        Normalized probability distribution summing to 1.0.
    """
    if not scores:
        return []

    effective_temp = max(temperature, 1e-9)  # prevent division by zero
    scaled = [s / effective_temp for s in scores]

    # Subtract max for numerical stability
    max_scaled = max(scaled)
    exps = [math.exp(s - max_scaled) for s in scaled]
    total = sum(exps)

    if total == 0.0:
        # Uniform distribution fallback
        n = len(scores)
        return [1.0 / n for _ in range(n)]

    return [e / total for e in exps]


def distribute_credit(
    surfaces: list[dict[str, object]],
    outcome_value: float,
    temperature: float = 1.0,
) -> list[CreditShare]:
    """Distribute credit proportionally among co-surfaced learnings.

    Uses softmax normalization over relevance scores (weighted combination
    of domain_match and temporal_proximity) to allocate credit shares
    that sum to 1.0.

    Args:
        surfaces: List of dicts with ``learning_id``, ``domain_match``
            (float 0-1), and ``temporal_proximity`` (float 0-1).
        outcome_value: The outcome value to attribute (used for logging).
        temperature: Softmax temperature. Lower = sharper distribution.

    Returns:
        List of CreditShare objects with shares summing to 1.0.
    """
    if not surfaces:
        return []

    relevance_scores = [_compute_relevance(s) for s in surfaces]
    shares = _softmax(relevance_scores, temperature)

    result = [
        CreditShare(
            learning_id=str(surfaces[i].get("learning_id", "")),
            share=shares[i],
            relevance_score=relevance_scores[i],
        )
        for i in range(len(surfaces))
    ]

    logger.debug(
        "credit_distributed",
        surface_count=len(surfaces),
        outcome_value=outcome_value,
        temperature=temperature,
    )

    return result
