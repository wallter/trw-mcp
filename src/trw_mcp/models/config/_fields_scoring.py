"""Scoring, impact distribution, utility decay, and outcome correlation fields.

Covers sections 8-10, 12 of the original _main_fields.py:
  - Impact score distribution (CORE-034)
  - Utility scoring & decay
  - Outcome correlation
  - Scoring subsystem
"""

from __future__ import annotations

from trw_mcp.models.config._defaults import DEFAULT_SCORING_DEFAULT_DAYS_UNUSED


class _ScoringFields:
    """Scoring domain mixin — mixed into _TRWConfigFields via MI."""

    # -- Impact score distribution (CORE-034) --

    impact_forced_distribution_enabled: bool = True
    impact_tier_critical_cap: float = 0.05
    impact_tier_high_cap: float = 0.20
    impact_high_threshold_pct: float = 20.0
    impact_decay_half_life_days: int = 90

    # -- Utility scoring & decay --

    learning_decay_half_life_days: float = 14.0
    learning_decay_use_exponent: float = 0.6
    learning_utility_prune_threshold: float = 0.10
    learning_utility_delete_threshold: float = 0.05
    q_learning_rate: float = 0.15
    q_recurrence_bonus: float = 0.02
    q_cold_start_threshold: int = 3
    source_human_utility_boost: float = 0.1
    access_count_utility_boost_cap: float = 0.15

    # -- Outcome correlation --

    learning_outcome_correlation_window_minutes: int = 480
    learning_outcome_correlation_scope: str = "session"
    learning_outcome_history_cap: int = 20
    recall_utility_lambda: float = 0.3

    # -- Scoring subsystem --

    scoring_default_days_unused: int = DEFAULT_SCORING_DEFAULT_DAYS_UNUSED
    scoring_recency_discount_floor: float = 0.5
    scoring_error_fallback_reward: float = -0.3
    scoring_error_keywords: tuple[str, ...] = ("error", "fail", "exception", "crash", "timeout")
