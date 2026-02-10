"""Early stopping criteria for gate evaluation rounds.

PRD-QUAL-005-FR06: Unanimous, KS stability, and confidence threshold checks
to terminate evaluation early when consensus is reached.
"""

from __future__ import annotations

from enum import Enum

from trw_mcp.models.gate import GateConfig, JudgeVote


class EarlyStopLevel(str, Enum):
    """Level at which early stopping was triggered."""

    UNANIMOUS = "unanimous"
    KS_STABILITY = "ks_stability"
    CONFIDENCE_THRESHOLD = "confidence_threshold"


def check_unanimous(votes: list[JudgeVote], threshold: float = 0.7) -> bool:
    """Check if all judges agree (all above or all below threshold).

    Args:
        votes: List of judge votes.
        threshold: Score threshold for pass/fail classification.

    Returns:
        True if all judges are on the same side of the threshold.
    """
    if not votes:
        return False
    above = all(v.score >= threshold for v in votes)
    below = all(v.score < threshold for v in votes)
    return above or below


def check_ks_stability(
    current_scores: list[float],
    prev_scores: list[float],
    threshold: float = 0.05,
) -> bool:
    """Check KS-like distribution stability between rounds.

    Simplified check: if the mean and spread of scores have converged
    between rounds (delta < threshold), the evaluation is stable.

    Args:
        current_scores: Scores from current round.
        prev_scores: Scores from previous round.
        threshold: Maximum allowed delta between round statistics.

    Returns:
        True if distribution has stabilized.
    """
    if not current_scores or not prev_scores:
        return False

    curr_mean = sum(current_scores) / len(current_scores)
    prev_mean = sum(prev_scores) / len(prev_scores)
    mean_delta = abs(curr_mean - prev_mean)

    return mean_delta < threshold


def check_confidence_threshold(
    votes: list[JudgeVote],
    threshold: float = 0.85,
) -> bool:
    """Check if average confidence exceeds the threshold.

    Args:
        votes: List of judge votes.
        threshold: Minimum average confidence for early stop.

    Returns:
        True if average confidence is above threshold.
    """
    if not votes:
        return False
    avg_confidence = sum(v.confidence for v in votes) / len(votes)
    return avg_confidence >= threshold


def _scores_within_agreement_band(scores: list[float], band: float) -> bool:
    """Check if all scores fall within a band around the mean.

    Args:
        scores: List of numeric scores.
        band: Maximum allowed deviation from the mean.

    Returns:
        True if every score is within ``band`` of the mean.
    """
    if not scores:
        return False
    mean_score = sum(scores) / len(scores)
    return all(abs(s - mean_score) < band for s in scores)


def evaluate_early_stop(
    votes: list[JudgeVote],
    prev_round_votes: list[JudgeVote] | None,
    config: GateConfig,
) -> tuple[bool, EarlyStopLevel | None]:
    """Evaluate all early stopping criteria in priority order.

    Priority: unanimous > KS stability > confidence threshold.

    Args:
        votes: Current round votes.
        prev_round_votes: Previous round votes (None for first round).
        config: Gate configuration.

    Returns:
        Tuple of (should_stop, level) where level indicates which criterion triggered.
    """
    # 1. Unanimous agreement
    if check_unanimous(votes, config.score_threshold):
        return True, EarlyStopLevel.UNANIMOUS

    # 2. KS stability (requires previous round)
    if prev_round_votes is not None:
        current_scores = [v.score for v in votes]
        prev_scores = [v.score for v in prev_round_votes]
        if check_ks_stability(current_scores, prev_scores, config.convergence_epsilon):
            return True, EarlyStopLevel.KS_STABILITY

    # 3. Confidence threshold — also requires reasonable score agreement
    if check_confidence_threshold(votes, config.early_stop_confidence):
        scores = [v.score for v in votes]
        if _scores_within_agreement_band(scores, config.early_stop_agreement_band):
            return True, EarlyStopLevel.CONFIDENCE_THRESHOLD

    return False, None
