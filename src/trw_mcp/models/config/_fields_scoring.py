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
    source_human_utility_boost: float = 0.1
    access_count_utility_boost_cap: float = 0.15

    # -- Outcome correlation --

    # PRD-FIX-088 FR04: lowered from 60 to 7 minutes.
    # 60-min window matched ~2800 receipts on active sessions (one
    # build_check correlated 2823 entries, taking 91s wall time).
    # 7 minutes covers a typical work cycle while keeping correlation
    # set sizes O(100). Existing deployments with an explicit
    # ``.trw/config.yaml`` override are unaffected (env > yaml > default).
    learning_outcome_correlation_window_minutes: int = 7
    learning_outcome_correlation_scope: str = "session"
    # CORE-116 RA2: targeted recall uses this only to enable (>0) or disable
    # (=0) secondary utility preferences; it cannot override query relevance.
    # Wildcard recall retains the historical relevance/utility blend.
    recall_utility_lambda: float = 0.3

    # -- Scoring subsystem --

    scoring_default_days_unused: int = DEFAULT_SCORING_DEFAULT_DAYS_UNUSED

    # proximal_reward_weight removed under PRD-CORE-291 (slice 2): no
    # production reader.

    # The skill lifecycle fields (PRD-QUAL-111) were removed with the skill-discovery tool,
    # their only reader (PRD-CORE-300 S11b); the keys are in config-retired-keys.json.
