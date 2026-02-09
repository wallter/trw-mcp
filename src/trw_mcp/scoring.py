"""Utility-based scoring for the TRW self-learning layer.

Pure, stateless functions for computing learning utility scores
and updating Q-values. These drive pruning decisions and recall ranking.

Research basis:
- MemRL Q-values (arXiv:2601.03192, Jan 2026)
- Ebbinghaus forgetting curve (CortexGraph, PowerMem)
- MACLA Bayesian selection (arXiv:2512.18950, Dec 2025)
"""

from __future__ import annotations

import math


def update_q_value(
    q_old: float,
    reward: float,
    alpha: float = 0.15,
    recurrence_bonus: float = 0.0,
) -> float:
    """Update Q-value using MemRL exponential moving average.

    Formula: Q_new = Q_old + alpha * (reward - Q_old) + recurrence_bonus

    Under stationary rewards, convergence guarantee:
    E[Q_t] = beta + (1-alpha)^t * (Q_0 - beta)
    where beta is the true expected reward.

    Args:
        q_old: Current Q-value for the learning entry (0.0-1.0).
        reward: Observed reward from outcome tracking (in [-1.0, 1.0]).
        alpha: Learning rate. Default 0.15 balances responsiveness
            with stability. Half-life of adaptation ~4.3 updates.
        recurrence_bonus: Small additive bonus when recurrence increases.
            Prevents Q-value from decaying for repeatedly-encountered issues.

    Returns:
        Updated Q-value, clamped to [0.0, 1.0].
    """
    q_new = q_old + alpha * (reward - q_old) + recurrence_bonus
    return max(0.0, min(1.0, q_new))


def compute_utility_score(
    q_value: float,
    days_since_last_access: int,
    recurrence_count: int,
    base_impact: float,
    q_observations: int,
    *,
    half_life_days: float = 14.0,
    use_exponent: float = 0.6,
    cold_start_threshold: int = 3,
) -> float:
    """Compute composite utility score combining Q-value with Ebbinghaus decay.

    The score determines both retrieval ranking and pruning eligibility.
    Higher scores = more valuable, less likely to be pruned, ranked higher.

    Formula:
        retention = recurrence_strength * exp(-effective_decay * days)
        effective_q = blend(impact, q_value, q_observations)
        utility = effective_q * retention

    Args:
        q_value: Current Q-value from outcome tracking (0.0-1.0).
        days_since_last_access: Days since last trw_recall retrieval.
            If never accessed, use days since creation.
        recurrence_count: Number of times the learning has been recalled.
            Minimum 1 (at creation).
        base_impact: Original static impact score (0.0-1.0).
        q_observations: Number of outcome observations for q_value.
        half_life_days: Days until retention halves without reinforcement.
            Default 14 (two weeks). Configurable via TRWConfig.
        use_exponent: Sub-linear exponent for recurrence count.
            Default 0.6 (from CortexGraph). Prevents over-reinforcement.
        cold_start_threshold: Number of Q-observations before fully
            trusting q_value over base_impact. Default 3.

    Returns:
        Composite utility score in [0.0, 1.0].
    """
    # Cold-start blending: transition from impact to q_value
    if q_observations < cold_start_threshold:
        w = q_observations / max(cold_start_threshold, 1)
        effective_q = (1.0 - w) * base_impact + w * q_value
    else:
        effective_q = q_value

    # Ebbinghaus decay rate from half-life: lambda = ln(2) / half_life
    decay_rate = math.log(2) / max(half_life_days, 0.1)

    # Sub-linear recurrence strength: n^beta (minimum 1)
    recurrence_strength = max(1.0, recurrence_count) ** use_exponent

    # Strength-modulated decay: higher recurrence = slower decay
    effective_decay = decay_rate / recurrence_strength
    retention = math.exp(-effective_decay * max(days_since_last_access, 0))

    # Composite score
    utility = effective_q * retention
    return max(0.0, min(1.0, utility))
