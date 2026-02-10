"""Escalation logic for gate evaluation on disagreement.

PRD-QUAL-005-FR07: Dynamic judge escalation when vote margins are tight,
with odd-number enforcement and configurable caps.
"""

from __future__ import annotations

from dataclasses import dataclass

from trw_mcp.models.gate import EscalationConfig, JudgeVote


@dataclass
class EscalationResult:
    """Result of an escalation decision."""

    should_escalate: bool
    additional_judges: int
    reason: str


def compute_vote_margin(votes: list[JudgeVote], threshold: float = 0.7) -> float:
    """Compute the margin between pass and fail votes.

    Margin = abs(fraction_above_threshold - 0.5) * 2
    Range: 0.0 (perfect split) to 1.0 (unanimous).

    Args:
        votes: List of judge votes.
        threshold: Score threshold for pass/fail classification.

    Returns:
        Vote margin from 0.0 to 1.0.
    """
    if not votes:
        return 0.0
    above = sum(1 for v in votes if v.score >= threshold)
    fraction = above / len(votes)
    return abs(fraction - 0.5) * 2


def ensure_odd(n: int) -> int:
    """Ensure n is odd (round up if even).

    Args:
        n: Number to check.

    Returns:
        Next odd number >= n.
    """
    if n % 2 == 0:
        return n + 1
    return n


def compute_escalation_judges(
    margin: float,
    current_n: int,
    config: EscalationConfig,
) -> int:
    """Compute how many additional judges to add based on vote margin.

    Near-tie (margin < config.near_tie_margin): +config.near_tie_judges
    Weak majority (margin < config.weak_majority_margin): +config.weak_majority_judges
    Otherwise: no escalation needed.

    Args:
        margin: Vote margin (0.0 = split, 1.0 = unanimous).
        current_n: Current number of judges.
        config: Escalation configuration with margin/judge thresholds.

    Returns:
        Number of additional judges to add (0 if no escalation needed).
    """
    if margin >= config.weak_majority_margin:
        return 0

    if margin < config.near_tie_margin:
        additional = config.near_tie_judges
    else:
        additional = config.weak_majority_judges

    additional = min(additional, max(0, config.max_total_judges - current_n))

    total = current_n + additional
    if total % 2 == 0 and total < config.max_total_judges:
        additional += 1

    return additional


def escalate(
    votes: list[JudgeVote],
    config: EscalationConfig,
    threshold: float = 0.7,
) -> EscalationResult:
    """Decide whether to escalate and by how many judges.

    Args:
        votes: Current round votes.
        config: Escalation configuration.
        threshold: Score threshold for pass/fail classification.

    Returns:
        EscalationResult with decision and judge count.
    """
    if not config.enabled:
        return EscalationResult(
            should_escalate=False,
            additional_judges=0,
            reason="Escalation disabled",
        )

    current_n = len(votes)
    if current_n >= config.max_total_judges:
        return EscalationResult(
            should_escalate=False,
            additional_judges=0,
            reason=f"Already at max judges ({config.max_total_judges})",
        )

    margin = compute_vote_margin(votes, threshold)
    additional = compute_escalation_judges(margin, current_n, config)

    if additional == 0:
        return EscalationResult(
            should_escalate=False,
            additional_judges=0,
            reason=f"Sufficient margin ({margin:.2f})",
        )

    return EscalationResult(
        should_escalate=True,
        additional_judges=additional,
        reason=f"Tight margin ({margin:.2f}), adding {additional} judges",
    )
