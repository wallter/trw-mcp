"""Promotion safety gate for CLAUDE.md and skill generation.

PRD-CORE-108-FR04: 5-criterion structural gate defending against
memory poisoning (MemoryGraft threat model).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger(__name__)

# Thresholds for promotion criteria
_ANCHOR_VALIDITY_THRESHOLD: float = 0.67
_MIN_SESSIONS_SURFACED: int = 3
_POSITIVE_OUTCOMES: frozenset[str] = frozenset({"positive", "strong_positive"})


@dataclass
class PromotionResult:
    """Result of the promotion safety gate evaluation."""

    passed: bool
    failures: list[str] = field(default_factory=list)
    force_promoted: bool = False


def _evaluate_criteria(
    learning: dict[str, object],
    graph_conflicts: list[str] | None = None,
) -> list[str]:
    """Evaluate all 5 promotion criteria and return list of failure reasons.

    Criteria:
    1. Provenance: non-empty ``detail`` field
    2. Anchor validity: ``anchor_validity >= 0.67``
    3. Independent sessions: ``sessions_surfaced >= 3``
    4. Outcome: ``outcome_correlation`` in ("positive", "strong_positive")
    5. No conflicts: ``graph_conflicts`` is empty or None

    Returns:
        List of failure reason strings. Empty list means all criteria pass.
    """
    failures: list[str] = []

    # 1. Provenance check: non-empty detail
    detail = str(learning.get("detail", "")).strip()
    if not detail:
        failures.append("Provenance: detail field is empty or missing")

    # 2. Anchor validity threshold
    anchor_raw = learning.get("anchor_validity")
    anchor_validity = 0.0
    if anchor_raw is not None:
        try:
            anchor_validity = float(str(anchor_raw))
        except (ValueError, TypeError):
            anchor_validity = 0.0
    if anchor_validity < _ANCHOR_VALIDITY_THRESHOLD:
        failures.append(
            f"Anchor validity: {anchor_validity:.2f} < {_ANCHOR_VALIDITY_THRESHOLD} threshold"
        )

    # 3. Independent sessions threshold
    sessions_raw = learning.get("sessions_surfaced")
    sessions_surfaced = 0
    if sessions_raw is not None:
        try:
            sessions_surfaced = int(str(sessions_raw))
        except (ValueError, TypeError):
            sessions_surfaced = 0
    if sessions_surfaced < _MIN_SESSIONS_SURFACED:
        failures.append(
            f"Sessions surfaced: {sessions_surfaced} < {_MIN_SESSIONS_SURFACED} required"
        )

    # 4. Outcome correlation must be positive
    outcome = str(learning.get("outcome_correlation", "")).strip()
    if outcome not in _POSITIVE_OUTCOMES:
        failures.append(
            f"Outcome correlation: '{outcome}' is not in {sorted(_POSITIVE_OUTCOMES)}"
        )

    # 5. No graph conflicts
    if graph_conflicts:
        failures.append(
            f"Graph conflicts: {len(graph_conflicts)} unresolved conflict(s)"
        )

    return failures


def check_promotion_gate(
    learning: dict[str, object],
    graph_conflicts: list[str] | None = None,
) -> PromotionResult:
    """Evaluate a learning against the 5-criterion promotion safety gate.

    All 5 criteria must pass for the learning to be eligible for
    promotion to CLAUDE.md or skill generation.

    Args:
        learning: Learning data dict with fields like ``detail``,
            ``anchor_validity``, ``sessions_surfaced``, ``outcome_correlation``.
        graph_conflicts: List of unresolved knowledge graph conflict IDs.

    Returns:
        PromotionResult with passed status, failures list, and
        force_promoted flag (always False for normal evaluation).
    """
    failures = _evaluate_criteria(learning, graph_conflicts)

    if failures:
        logger.debug(
            "promotion_gate_rejected",
            learning_id=str(learning.get("id", "unknown")),
            failure_count=len(failures),
        )

    return PromotionResult(
        passed=len(failures) == 0,
        failures=failures,
        force_promoted=False,
    )


def force_promote(
    learning: dict[str, object],
    reason: str,
    agent_identity: str,
) -> PromotionResult:
    """Force-promote a learning, logging warnings for failed criteria.

    Evaluates all 5 criteria but allows promotion regardless.
    Each failed criterion is logged as a WARNING with the agent identity.

    Args:
        learning: Learning data dict.
        reason: Reason for the force promotion.
        agent_identity: Identity of the agent forcing the promotion.

    Returns:
        PromotionResult with passed=True, any failures recorded,
        and force_promoted=True.
    """
    failures = _evaluate_criteria(learning)

    for failure in failures:
        logger.warning(
            "force_promotion_criterion_failed",
            learning_id=str(learning.get("id", "unknown")),
            failure=failure,
            reason=reason,
            agent_identity=agent_identity,
        )

    if failures:
        logger.warning(
            "force_promotion_override",
            learning_id=str(learning.get("id", "unknown")),
            failure_count=len(failures),
            reason=reason,
            agent_identity=agent_identity,
        )

    return PromotionResult(
        passed=True,
        failures=failures,
        force_promoted=True,
    )
