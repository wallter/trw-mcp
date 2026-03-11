"""Utility-based scoring for the TRW self-learning layer.

Core scoring functions (compute_utility_score, update_q_value) plus
outcome correlation, recall ranking, and pruning candidate identification
extracted from tools/learning.py (PRD-FIX-010).

Research basis:
- MemRL Q-values (arXiv:2601.03192, Jan 2026)
- Ebbinghaus forgetting curve (CortexGraph, PowerMem)
- MACLA Bayesian selection (arXiv:2512.18950, Dec 2025)

This package was decomposed from a monolithic ``scoring.py`` module.
All public names are re-exported here for backward compatibility --
existing ``from trw_mcp.scoring import X`` imports continue to work.
"""

from __future__ import annotations

# --- Re-exports from _utils (constants, trw_memory delegates, shared state) ---
from trw_mcp.models.config import get_config as get_config
from trw_mcp.scoring._utils import (
    _IMPACT_DECAY_FLOOR as _IMPACT_DECAY_FLOOR,
    _LN2 as _LN2,
    _TIER_HIGH_CEILING as _TIER_HIGH_CEILING,
    _TIER_MEDIUM_CEILING as _TIER_MEDIUM_CEILING,
    _clamp01 as _clamp01,
    _ensure_utc as _ensure_utc,
    apply_time_decay as apply_time_decay,
    bayesian_calibrate as bayesian_calibrate,
    compute_calibration_accuracy as compute_calibration_accuracy,
    compute_utility_score as compute_utility_score,
    logger as logger,
    resolve_trw_dir as resolve_trw_dir,
    safe_float as safe_float,
    safe_int as safe_int,
    update_q_value as update_q_value,
)
from trw_mcp.state.persistence import (
    FileStateReader as FileStateReader,
    FileStateWriter as FileStateWriter,
)

# --- Re-exports from _complexity ---
from trw_mcp.scoring._complexity import (
    _HIGH_RISK_SIGNALS as _HIGH_RISK_SIGNALS,
    _TIER_EXPECTATIONS as _TIER_EXPECTATIONS,
    _TierExpectation as _TierExpectation,
    classify_complexity as classify_complexity,
    compute_tier_ceremony_score as compute_tier_ceremony_score,
    get_phase_requirements as get_phase_requirements,
)

# --- Re-exports from _decay ---
from trw_mcp.scoring._decay import (
    _days_since_access as _days_since_access,
    _entry_utility as _entry_utility,
    apply_impact_decay as apply_impact_decay,
    compute_impact_distribution as compute_impact_distribution,
    enforce_tier_distribution as enforce_tier_distribution,
)

# --- Re-exports from _recall ---
from trw_mcp.scoring._recall import (
    rank_by_utility as rank_by_utility,
    utility_based_prune_candidates as utility_based_prune_candidates,
)

# --- Re-exports from _correlation ---
from trw_mcp.scoring._correlation import (
    EVENT_ALIASES as EVENT_ALIASES,
    REWARD_MAP as REWARD_MAP,
    _find_session_start_ts as _find_session_start_ts,
    _resolve_event_reward as _resolve_event_reward,
    correlate_recalls as correlate_recalls,
    process_outcome as process_outcome,
    process_outcome_for_event as process_outcome_for_event,
)

def __getattr__(name: str) -> object:
    """Backward-compat shim for removed module-level singletons (FIX-044)."""
    if name == "_config":
        return get_config()
    if name == "_reader":
        return FileStateReader()
    if name == "_writer":
        return FileStateWriter()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # trw_memory re-exports
    "_clamp01",
    "_ensure_utc",
    "apply_time_decay",
    "bayesian_calibrate",
    "compute_calibration_accuracy",
    "compute_utility_score",
    "update_q_value",
    # Complexity / ceremony
    "_HIGH_RISK_SIGNALS",
    "_TIER_EXPECTATIONS",
    "_TierExpectation",
    "classify_complexity",
    "compute_tier_ceremony_score",
    "get_phase_requirements",
    # Decay / distribution
    "_days_since_access",
    "_entry_utility",
    "apply_impact_decay",
    "compute_impact_distribution",
    "enforce_tier_distribution",
    # Recall / ranking
    "rank_by_utility",
    "utility_based_prune_candidates",
    # Correlation / Q-learning
    "EVENT_ALIASES",
    "REWARD_MAP",
    "_find_session_start_ts",
    "_resolve_event_reward",
    "correlate_recalls",
    "process_outcome",
    "process_outcome_for_event",
]
