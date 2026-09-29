"""Forced impact-tier distribution enforcement (PRD-CORE-034).

Belongs to the ``trw_mcp.scoring`` facade, which re-exports
``enforce_tier_distribution``. The algorithm lives in trw-memory; this
adapter resolves omitted caps from ``TRWConfig``.
"""

from __future__ import annotations

from trw_memory.lifecycle.scoring import enforce_tier_distribution as _memory_enforce_tier_distribution
from trw_memory.models.config import MemoryConfig

from trw_mcp.scoring._utils import TRWConfig, get_config

# --- Forced distribution enforcement (PRD-CORE-034) ---


def enforce_tier_distribution(
    entries: list[tuple[str, float]],
    *,
    critical_cap: float | None = None,
    high_cap: float | None = None,
    entry_dates: dict[str, str] | None = None,
) -> list[tuple[str, float]]:
    """Enforce the impact-tier caps (critical >5%, high >20% by default) on active learnings.

    The algorithm is trw-memory's :func:`trw_memory.lifecycle.scoring.enforce_tier_distribution`
    (one demotion per tier per call, time decay used for classification only, absolute demotion
    targets 0.89/0.69); this adapter only resolves an omitted cap from ``TRWConfig`` so the
    ``.trw/config.yaml`` ``impact_tier_*_cap`` knobs keep governing trw-mcp.

    Args:
        entries: ``(learning_id, impact_score)`` pairs; the caller filters to active learnings.
        critical_cap: Critical-tier cap; ``None`` reads ``impact_tier_critical_cap``.
        high_cap: High-tier cap; ``None`` reads ``impact_tier_high_cap``.
        entry_dates: Optional ``learning_id -> ISO date`` map for decay-aware classification.

    Returns:
        ``(learning_id, new_impact)`` for each demoted entry; empty when none is needed.
    """
    cfg: TRWConfig = get_config()
    return _memory_enforce_tier_distribution(
        entries,
        critical_cap=cfg.impact_tier_critical_cap if critical_cap is None else critical_cap,
        high_cap=cfg.impact_tier_high_cap if high_cap is None else high_cap,
        entry_dates=entry_dates,
        # Both caps are explicit, so no MemoryConfig setting is read; model_construct() skips its
        # env/validation so a bad trw-memory environment cannot fail this trw-mcp call.
        config=MemoryConfig.model_construct(),
    )


__all__ = ["enforce_tier_distribution"]
