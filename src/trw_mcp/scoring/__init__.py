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
)
from trw_mcp.scoring._complexity import (
    _TIER_EXPECTATIONS as _TIER_EXPECTATIONS,
)
from trw_mcp.scoring._complexity import (
    CeremonyDepthContract as CeremonyDepthContract,
)
from trw_mcp.scoring._complexity import (
    _TierExpectation as _TierExpectation,
)
from trw_mcp.scoring._complexity import (
    classify_complexity as classify_complexity,
)
from trw_mcp.scoring._complexity import (
    compute_tier_ceremony_score as compute_tier_ceremony_score,
)
from trw_mcp.scoring._complexity import (
    get_ceremony_depth_contract as get_ceremony_depth_contract,
)
from trw_mcp.scoring._complexity import (
    get_phase_requirements as get_phase_requirements,
)
from trw_mcp.scoring._correlation import (
    EVENT_ALIASES as EVENT_ALIASES,
)
from trw_mcp.scoring._correlation import (
    REWARD_MAP as REWARD_MAP,
)
from trw_mcp.scoring._correlation import (
    _find_session_start_ts as _find_session_start_ts,
)
from trw_mcp.scoring._correlation import (
    _resolve_event_reward as _resolve_event_reward,
)
from trw_mcp.scoring._correlation import (
    compute_composite_outcome as compute_composite_outcome,
)
from trw_mcp.scoring._correlation import (
    compute_initial_q_value as compute_initial_q_value,
)
from trw_mcp.scoring._correlation import (
    correlate_recalls as correlate_recalls,
)
from trw_mcp.scoring._correlation import (
    process_outcome as process_outcome,
)
from trw_mcp.scoring._correlation import (
    process_outcome_for_event as process_outcome_for_event,
)
from trw_mcp.scoring._correlation import (
    sigmoid_normalize as sigmoid_normalize,
)
from trw_mcp.scoring._decay import (
    _days_since_access as _days_since_access,
)
from trw_mcp.scoring._decay import (
    _entry_utility as _entry_utility,
)
from trw_mcp.scoring._decay import (
    apply_impact_decay as apply_impact_decay,
)
from trw_mcp.scoring._decay import (
    compute_impact_distribution as compute_impact_distribution,
)
from trw_mcp.scoring._decay import (
    enforce_tier_distribution as enforce_tier_distribution,
)
from trw_mcp.scoring._recall import (
    RecallContext as RecallContext,
)
from trw_mcp.scoring._recall import (
    infer_domains as infer_domains,
)
from trw_mcp.scoring._recall import (
    rank_by_utility as rank_by_utility,
)
from trw_mcp.scoring._recall import (
    utility_based_prune_candidates as utility_based_prune_candidates,
)
from trw_mcp.scoring._utils import (
    _IMPACT_DECAY_FLOOR as _IMPACT_DECAY_FLOOR,
)
from trw_mcp.scoring._utils import (
    _LN2 as _LN2,
)
from trw_mcp.scoring._utils import (
    _TIER_HIGH_CEILING as _TIER_HIGH_CEILING,
)
from trw_mcp.scoring._utils import (
    _TIER_MEDIUM_CEILING as _TIER_MEDIUM_CEILING,
)
from trw_mcp.scoring._utils import (
    _clamp01 as _clamp01,
)
from trw_mcp.scoring._utils import (
    _ensure_utc as _ensure_utc,
)
from trw_mcp.scoring._utils import (
    apply_time_decay as apply_time_decay,
)
from trw_mcp.scoring._utils import (
    bayesian_calibrate as bayesian_calibrate,
)
from trw_mcp.scoring._utils import (
    compute_calibration_accuracy as compute_calibration_accuracy,
)
from trw_mcp.scoring._utils import (
    compute_utility_score as compute_utility_score,
)
from trw_mcp.scoring._utils import (
    safe_float as safe_float,
)
from trw_mcp.scoring._utils import (
    safe_int as safe_int,
)
from trw_mcp.scoring._utils import (
    update_q_value as update_q_value,
)
from trw_mcp.scoring.proximal_reward import (
    detect_proximal_signals as detect_proximal_signals,
)
from trw_mcp.scoring.rework_rate import (
    compute_rework_rate as compute_rework_rate,
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
    "CeremonyDepthContract",
    "RecallContext",
    "apply_impact_decay",
    "apply_time_decay",
    "bayesian_calibrate",
    "classify_complexity",
    "compute_calibration_accuracy",
    "compute_composite_outcome",
    "compute_impact_distribution",
    "compute_initial_q_value",
    "compute_rework_rate",
    "compute_tier_ceremony_score",
    "compute_utility_score",
    "correlate_recalls",
    "detect_proximal_signals",
    "enforce_tier_distribution",
    "get_ceremony_depth_contract",
    "get_phase_requirements",
    "infer_domains",
    "process_outcome",
    "process_outcome_for_event",
    "rank_by_utility",
    "safe_float",
    "safe_int",
    "sigmoid_normalize",
    "update_q_value",
    "utility_based_prune_candidates",
]
