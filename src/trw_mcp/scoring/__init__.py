"""Utility-based scoring for the TRW self-learning layer.

Core scoring functions (compute_utility_score) plus
outcome correlation, recall ranking, and pruning candidate identification
extracted from tools/learning.py (PRD-FIX-010).

Research basis:
- Ebbinghaus forgetting curve (CortexGraph, PowerMem)

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
    CeremonyDepthContract as CeremonyDepthContract,
)
from trw_mcp.scoring._complexity import (
    _TierExpectation as _TierExpectation,
)
from trw_mcp.scoring._complexity import (
    classify_complexity as classify_complexity,
)
from trw_mcp.scoring._complexity import (
    get_ceremony_depth_contract as get_ceremony_depth_contract,
)
from trw_mcp.scoring._complexity import (
    get_phase_requirements as get_phase_requirements,
)
from trw_mcp.scoring._correlation import (
    _find_session_start_ts as _find_session_start_ts,
)
from trw_mcp.scoring._correlation import (
    correlate_recalls as correlate_recalls,
)
from trw_mcp.scoring._decay import (
    _days_since_access as _days_since_access,
)
from trw_mcp.scoring._decay import (
    entry_utility as entry_utility,
)
from trw_mcp.scoring._decay import (
    utility_params_for as utility_params_for,
)
from trw_mcp.scoring._recall import (
    RecallContext as RecallContext,
)
from trw_mcp.scoring._recall import (
    infer_domains as infer_domains,
)
from trw_mcp.scoring._recall import (
    rank_targeted_by_utility as rank_targeted_by_utility,
)
from trw_mcp.scoring._recall import (
    utility_based_prune_candidates as utility_based_prune_candidates,
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
    compute_utility_score as compute_utility_score,
)
from trw_mcp.scoring._utils import (
    safe_float as safe_float,
)
from trw_mcp.scoring._utils import (
    safe_int as safe_int,
)
from trw_mcp.scoring.rework_rate import (
    compute_rework_rate as compute_rework_rate,
)

__all__ = [
    "CeremonyDepthContract",
    "RecallContext",
    "apply_time_decay",
    "classify_complexity",
    "compute_rework_rate",
    "compute_utility_score",
    "correlate_recalls",
    "get_ceremony_depth_contract",
    "get_phase_requirements",
    "infer_domains",
    "rank_targeted_by_utility",
    "safe_float",
    "safe_int",
    "utility_based_prune_candidates",
]
