"""Impact-tier distribution analysis and forced-distribution enforcement.

PRD-CORE-034: Impact scoring with forced distribution.

Belongs to the ``trw_mcp.scoring`` facade. The public names
(``compute_impact_distribution``, ``enforce_tier_distribution``) are
re-exported there for back-compat. Split out of ``_decay.py`` so that the
time-decay/utility concern and the tier-classification concern each stay a
focused deep module under the 350-line gate.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import trw_mcp.scoring._utils as _su
from trw_mcp.models.typed_dicts import ImpactDistributionResult, ImpactTierInfo
from trw_mcp.scoring._io_boundary import (
    _load_entries_from_dir as _load_entries_from_dir,
)
from trw_mcp.scoring._utils import (
    _TIER_HIGH_CEILING,
    _TIER_MEDIUM_CEILING,
    TRWConfig,
    apply_time_decay,
    get_config,
    safe_float,
)

# --- Impact distribution analysis (PRD-CORE-034) ---


def _compute_distribution_from_entries(
    entries: Iterable[dict[str, object]],
) -> ImpactDistributionResult:
    """Compute impact distribution from pre-loaded entry dicts.

    PRD-FIX-061-FR04: Pure scoring function that operates on an iterable
    of entry dicts instead of performing file I/O.  Only active entries
    (status == "active" or missing) are counted.

    Args:
        entries: Iterable of learning entry dicts with at least
            ``status`` and ``impact`` keys.

    Returns:
        Typed dict with tier counts and percentages.
    """
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    total = 0

    for data in entries:
        if str(data.get("status", "active")) != "active":
            continue
        score = safe_float(data, "impact", 0.5)
        total += 1
        if score >= 0.9:
            counts["critical"] += 1
        elif score >= 0.7:
            counts["high"] += 1
        elif score >= 0.4:
            counts["medium"] += 1
        else:
            counts["low"] += 1

    def _pct(n: int) -> float:
        return round(n / total, 4) if total > 0 else 0.0

    return ImpactDistributionResult(
        total_active=total,
        critical=ImpactTierInfo(count=counts["critical"], pct=_pct(counts["critical"])),
        high=ImpactTierInfo(count=counts["high"], pct=_pct(counts["high"])),
        medium=ImpactTierInfo(count=counts["medium"], pct=_pct(counts["medium"])),
        low=ImpactTierInfo(count=counts["low"], pct=_pct(counts["low"])),
    )


def compute_impact_distribution(
    entries_dir: Path,
) -> ImpactDistributionResult:
    """Compute the current impact score distribution across active learnings.

    PRD-FIX-061-FR04: File I/O is now at the boundary -- the actual scoring
    logic is in ``_compute_distribution_from_entries()``.  This function
    provides backward-compatible Path-based entry point.

    Args:
        entries_dir: Path to the entries directory containing YAML files.

    Returns:
        Dict with tier counts and percentages:
        {
            "total_active": int,
            "critical": {"count": int, "pct": float},  # 0.9-1.0
            "high": {"count": int, "pct": float},       # 0.7-0.89
            "medium": {"count": int, "pct": float},     # 0.4-0.69
            "low": {"count": int, "pct": float},        # 0.0-0.39
        }
    """
    _zero: ImpactTierInfo = {"count": 0, "pct": 0.0}
    if not entries_dir.exists():
        return ImpactDistributionResult(
            total_active=0,
            critical=_zero,
            high=_zero,
            medium=_zero,
            low=_zero,
        )

    return _compute_distribution_from_entries(_load_entries_from_dir(entries_dir))


# --- Forced distribution enforcement (PRD-CORE-034) ---


def enforce_tier_distribution(
    entries: list[tuple[str, float]],
    *,
    critical_cap: float | None = None,
    high_cap: float | None = None,
    entry_dates: dict[str, str] | None = None,
) -> list[tuple[str, float]]:
    """Enforce forced distribution caps on impact tier percentages.

    When a tier exceeds its cap (critical >5%, high >20%), demotes the
    lowest-scored entry in that tier to the next tier down.  Only one
    demotion per tier per call -- callers may iterate to convergence if
    desired.

    Args:
        entries: List of (learning_id, impact_score) tuples.  Only active
            learnings should be included; caller is responsible for filtering.
        critical_cap: Maximum fraction allowed in critical tier (0.9-1.0).
            Defaults to config value.
        high_cap: Maximum fraction allowed in high tier (0.7-0.89).
            Defaults to config value.
        entry_dates: Optional mapping of learning_id -> ISO date string
            (e.g. "2025-08-01T12:00:00"). When provided, time decay is
            applied to each score before tier classification so that older
            high-impact learnings are not kept in upper tiers indefinitely.
            The demotion target scores (0.89, 0.69) remain absolute --
            decay only affects which entries are classified into each tier.

    Returns:
        List of (learning_id, new_impact) tuples for entries whose scores
        were changed.  Empty list if no demotions were needed.
    """
    cfg: TRWConfig = get_config()
    effective_critical_cap = critical_cap if critical_cap is not None else cfg.impact_tier_critical_cap
    effective_high_cap = high_cap if high_cap is not None else cfg.impact_tier_high_cap

    if not entries:
        return []

    total = len(entries)

    # Don't enforce distribution on very small sets -- percentage caps are
    # meaningless with fewer than 5 active learnings.
    if total < 5:
        return []

    # Build decayed score lookup for tier classification only.
    # When entry_dates is None, decayed == original (backward compat).
    def _decayed_score(lid: str, score: float) -> float:
        if entry_dates is None:
            return score
        date_str = entry_dates.get(lid, "")
        if not date_str:
            return score
        try:
            created_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            # query-time only -- does not write to disk (PRD-FIX-027-FR06)
            return apply_time_decay(score, created_dt)
        except ValueError:
            return score

    # Separate into tiers using decayed scores for classification,
    # but store original scores so demotions use absolute targets.
    critical: list[tuple[str, float]] = []
    high: list[tuple[str, float]] = []

    for lid, score in entries:
        tier_score = _decayed_score(lid, score)
        if tier_score >= 0.9:
            critical.append((lid, score))
        elif tier_score >= 0.7:
            high.append((lid, score))

    demotions: list[tuple[str, float]] = []

    # Enforce critical cap: demote lowest-scored critical -> high
    if critical and len(critical) / total > effective_critical_cap:
        # Sort ascending by score to find lowest
        critical_sorted = sorted(critical, key=lambda x: x[1])
        victim_id, victim_score = critical_sorted[0]
        # Demote to top of high tier
        new_score = round(min(_TIER_HIGH_CEILING, max(0.7, victim_score - 0.1)), 4)
        demotions.append((victim_id, new_score))
        _su.logger.info(
            "tier_demotion",
            learning_id=victim_id,
            from_tier="critical",
            to_tier="high",
            old_score=victim_score,
            new_score=new_score,
        )

    # Re-compute high count after potential demotion from critical
    demoted_ids = {d[0] for d in demotions}
    effective_high = [e for e in high if e[0] not in demoted_ids]
    # Add demoted critical entries to high count
    effective_high_count = len(effective_high) + len(demotions)

    # Enforce high cap: demote lowest-scored high -> medium
    if effective_high_count > 0 and effective_high_count / total > effective_high_cap:
        high_sorted = sorted(
            [(lid, s) for lid, s in high if lid not in demoted_ids],
            key=lambda x: x[1],
        )
        if high_sorted:
            victim_id, victim_score = high_sorted[0]
            new_score = round(min(_TIER_MEDIUM_CEILING, max(0.4, victim_score - 0.1)), 4)
            demotions.append((victim_id, new_score))
            _su.logger.info(
                "tier_demotion",
                learning_id=victim_id,
                from_tier="high",
                to_tier="medium",
                old_score=victim_score,
                new_score=new_score,
            )

    return demotions


__all__ = [
    "_compute_distribution_from_entries",
    "_load_entries_from_dir",
    "compute_impact_distribution",
    "enforce_tier_distribution",
]
