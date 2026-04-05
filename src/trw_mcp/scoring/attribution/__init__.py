"""Causal outcome attribution for the TRW self-learning layer.

PRD-CORE-108: DML + Causal Estimation Pipeline.

This sub-package implements a multi-tier attribution system that runs
at deliver time to assign causal credit to surfaced learnings:

- **Tier 1 (IPS)**: Inverse Propensity Scoring when exploration data exists
- **Tier 2 (DML)**: Double Machine Learning via EconML (optional dependency)
- **Selective attribution**: ERM-style credit splitting among co-surfaced learnings
- **Phase eligibility**: Phase-distance traces to prevent end-of-session bias
- **Promotion gate**: 5-criterion structural safety gate for CLAUDE.md promotion
"""

from __future__ import annotations

from trw_mcp.scoring.attribution.eligibility import (
    compute_phase_weight as compute_phase_weight,
)
from trw_mcp.scoring.attribution.ips import (
    AttributionResult as AttributionResult,
)
from trw_mcp.scoring.attribution.ips import (
    compute_ips_attribution as compute_ips_attribution,
)
from trw_mcp.scoring.attribution.pipeline import (
    run_attribution as run_attribution,
)
from trw_mcp.scoring.attribution.promotion import (
    PromotionResult as PromotionResult,
)
from trw_mcp.scoring.attribution.promotion import (
    check_promotion_gate as check_promotion_gate,
)
from trw_mcp.scoring.attribution.promotion import (
    force_promote as force_promote,
)
from trw_mcp.scoring.attribution.selective import (
    CreditShare as CreditShare,
)
from trw_mcp.scoring.attribution.selective import (
    distribute_credit as distribute_credit,
)

__all__ = [
    "AttributionResult",
    "CreditShare",
    "PromotionResult",
    "check_promotion_gate",
    "compute_ips_attribution",
    "compute_phase_weight",
    "distribute_credit",
    "force_promote",
    "run_attribution",
]
