"""Phase-distance eligibility traces for credit assignment.

PRD-CORE-108-FR03: Uses phase hops rather than temporal distance
to prevent end-of-session bias.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)

# Phase ordering: index represents the phase position in the pipeline
_PHASE_ORDER: dict[str, int] = {
    "RESEARCH": 0,
    "PLAN": 1,
    "IMPLEMENT": 2,
    "VALIDATE": 3,
    "REVIEW": 4,
    "DELIVER": 5,
}


def compute_phase_weight(
    source_phase: str,
    target_phase: str,
    decay_factor: float = 0.7,
) -> float:
    """Compute eligibility weight based on phase distance.

    Weight decays exponentially with the number of phase hops between
    source and target. This prevents end-of-session bias by using
    structural distance (phases) rather than temporal distance (time).

    Args:
        source_phase: Phase where the learning was surfaced (e.g. "RESEARCH").
        target_phase: Phase where the outcome occurred (e.g. "VALIDATE").
        decay_factor: Exponential decay base per hop (default 0.7).

    Returns:
        Weight in (0, 1]. Unknown phases return 1.0 as safe default.
    """
    source_idx = _PHASE_ORDER.get(source_phase.upper())
    target_idx = _PHASE_ORDER.get(target_phase.upper())

    if source_idx is None or target_idx is None:
        logger.debug(
            "phase_weight_unknown_phase",
            source_phase=source_phase,
            target_phase=target_phase,
        )
        return 1.0

    distance = abs(target_idx - source_idx)
    weight = decay_factor ** distance

    return weight
