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

# --- Public re-exports from sub-modules ---
from trw_mcp.scoring._complexity import (
    _HIGH_RISK_SIGNALS as _HIGH_RISK_SIGNALS,
    _TIER_EXPECTATIONS as _TIER_EXPECTATIONS,
    _TierExpectation as _TierExpectation,
    classify_complexity as classify_complexity,
    compute_tier_ceremony_score as compute_tier_ceremony_score,
    get_phase_requirements as get_phase_requirements,
)
from trw_mcp.scoring._correlation import (
    EVENT_ALIASES as EVENT_ALIASES,
    REWARD_MAP as REWARD_MAP,
    _find_session_start_ts as _find_session_start_ts,
    _resolve_event_reward as _resolve_event_reward,
    compute_initial_q_value as compute_initial_q_value,
    correlate_recalls as correlate_recalls,
    process_outcome as process_outcome,
    process_outcome_for_event as process_outcome_for_event,
)
from trw_mcp.scoring._decay import (
    _days_since_access as _days_since_access,
    _entry_utility as _entry_utility,
    apply_impact_decay as apply_impact_decay,
    compute_impact_distribution as compute_impact_distribution,
    enforce_tier_distribution as enforce_tier_distribution,
)
from trw_mcp.scoring._recall import (
    rank_by_utility as rank_by_utility,
    utility_based_prune_candidates as utility_based_prune_candidates,
)
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
    safe_float as safe_float,
    safe_int as safe_int,
    update_q_value as update_q_value,
)


def __getattr__(name: str) -> object:
    """Backward-compat shim for test module-level singleton patching (FIX-044).

    Tests may patch module attributes like _config, _reader, _writer directly
    on this module. This shim provides lazy construction so the attributes
    exist on first access, enabling those patches to work.

    Note: Production code should never rely on this — use get_config(),
    FileStateReader(), FileStateWriter() directly.

    Raises:
        AttributeError: If name is not one of the known test singletons.
    """
    from trw_mcp.state._helpers import _compat_getattr

    return _compat_getattr(name)


__all__ = [
    "EVENT_ALIASES",
    "REWARD_MAP",
    "apply_impact_decay",
    "apply_time_decay",
    "bayesian_calibrate",
    "classify_complexity",
    "compute_calibration_accuracy",
    "compute_impact_distribution",
    "compute_initial_q_value",
    "compute_tier_ceremony_score",
    "compute_utility_score",
    "correlate_recalls",
    "enforce_tier_distribution",
    "get_phase_requirements",
    "process_outcome",
    "process_outcome_for_event",
    "rank_by_utility",
    "safe_float",
    "safe_int",
    "update_q_value",
    "utility_based_prune_candidates",
]
