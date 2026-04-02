"""Ebbinghaus decay, impact distribution, and tier enforcement.

PRD-CORE-034: Impact scoring with exponential decay and forced distribution.

Internal module -- all public names are re-exported from ``trw_mcp.scoring``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator
from datetime import date, datetime, timezone
from pathlib import Path

import trw_mcp.scoring._utils as _su
from trw_mcp.models.typed_dicts import ImpactDistributionResult, ImpactTierInfo, LearningEntryDict
from trw_mcp.scoring._utils import (
    _IMPACT_DECAY_FLOOR,
    _LN2,
    _TIER_HIGH_CEILING,
    _TIER_MEDIUM_CEILING,
    TRWConfig,
    _ensure_utc,
    apply_time_decay,
    compute_utility_score,
    get_config,
    safe_float,
    safe_int,
)
from trw_mcp.state._helpers import iter_yaml_entry_files
from trw_mcp.state.persistence import FileStateReader

# PRD-CORE-004: Utility-based impact scoring (Q-learning, Ebbinghaus decay)


def _days_since_access(
    entry: dict[str, object],
    today: date,
    fallback_days: int | None = None,
) -> int:
    """Compute days since last access, falling back to creation date.

    Resolution order: last_accessed_at -> created -> fallback_days.
    """
    if fallback_days is None:
        cfg: TRWConfig = get_config()
        fallback_days = cfg.scoring_default_days_unused

    for field in ("last_accessed_at", "created"):
        raw = str(entry.get(field, ""))
        if not raw or raw == "None":
            continue
        try:
            return (today - date.fromisoformat(raw.replace("Z", "+00:00"))).days
        except ValueError:
            continue

    return fallback_days


# Type-aware decay half-lives (PRD-CORE-102)
_TYPE_HALF_LIFE: dict[str, float] = {
    "incident": 90.0,       # Incidents stay relevant longer (postmortem knowledge)
    "convention": 365.0,    # Conventions are near-permanent
    "pattern": 30.0,        # Patterns decay at moderate rate
    "hypothesis": 7.0,      # Hypotheses should be validated quickly
    "workaround": 14.0,     # Workarounds are temporary by nature
}


def _type_half_life(entry_type: str, cfg: TRWConfig) -> float:
    """Get decay half-life based on learning type.

    Falls back to config default for unrecognized types.
    """
    return _TYPE_HALF_LIFE.get(entry_type, cfg.learning_decay_half_life_days)


def _entry_utility(
    entry: dict[str, object],
    today: date,
    fallback_days: int | None = None,
) -> float:
    """Compute utility score with type-aware decay curves.

    Extracts scoring fields from the entry dict and delegates to
    compute_utility_score with TRWConfig parameters.

    PRD-CORE-034: Applies time decay to base_impact before computing utility
    so that older learnings naturally sink in recall ranking results.
    PRD-CORE-102: Uses type-aware half-life for more accurate decay.
    """
    q_value = safe_float(entry, "q_value", safe_float(entry, "impact", 0.5))
    base_impact = safe_float(entry, "impact", 0.5)
    q_observations = safe_int(entry, "q_observations", 0)
    recurrence = safe_int(entry, "recurrence", 1)
    access_count = safe_int(entry, "access_count", 0)
    source_type = str(entry.get("source_type", "agent"))
    days_unused = _days_since_access(entry, today, fallback_days=fallback_days)

    cfg: TRWConfig = get_config()

    # Type-aware half-life (PRD-CORE-102)
    entry_type = str(entry.get("type", ""))
    half_life = _type_half_life(entry_type, cfg)

    # Unverified incidents don't decay (preserve postmortem knowledge until verified)
    entry_confidence = str(entry.get("confidence", "unverified"))
    if entry_type == "incident" and entry_confidence == "unverified":
        half_life = 9999.0  # Effectively no decay

    # Check expiry (PRD-CORE-110)
    expires_str = str(entry.get("expires", ""))
    if expires_str:
        try:
            expires_date = date.fromisoformat(expires_str.replace("Z", "+00:00"))
            if today > expires_date:
                return 0.01  # Expired → demote to very low utility
        except ValueError:
            pass  # Malformed expiry, ignore

    return compute_utility_score(
        q_value=q_value,
        days_since_last_access=days_unused,
        recurrence_count=recurrence,
        base_impact=base_impact,
        q_observations=q_observations,
        half_life_days=half_life,
        use_exponent=cfg.learning_decay_use_exponent,
        cold_start_threshold=cfg.q_cold_start_threshold,
        access_count=access_count,
        source_type=source_type,
        access_count_boost_cap=cfg.access_count_utility_boost_cap,
        source_human_boost=cfg.source_human_utility_boost,
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


def _load_entries_from_dir(entries_dir: Path) -> Iterator[dict[str, object]]:
    """Load entry dicts from a YAML entries directory.

    Yields parsed dicts for each readable YAML entry file.
    Silently skips files that fail to parse.

    Args:
        entries_dir: Directory containing YAML entry files.

    Yields:
        Parsed entry dicts.
    """
    reader = FileStateReader()
    for yaml_file in iter_yaml_entry_files(entries_dir):
        try:
            yield reader.read_yaml(yaml_file)
        except Exception:  # justified: fail-open, skip unreadable YAML entries  # noqa: S112, PERF203
            continue


def compute_impact_distribution(
    entries_dir: Path,
) -> ImpactDistributionResult:
    """Compute the current impact score distribution across active learnings.

    PRD-FIX-061-FR04: File I/O is now at the boundary — the actual scoring
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


# --- Ebbinghaus decay for impact scores (PRD-CORE-034) ---


def apply_impact_decay(
    entries: list[LearningEntryDict],
    half_life_days: int | None = None,
) -> None:
    """Apply exponential impact decay to stale learnings **in-place** (PRD-CORE-034-FR03).

    For each entry, reads ``last_accessed`` (or ``created``) date and computes
    days since that date.  If days_since exceeds ``half_life_days``, the impact
    is decayed using an exponential formula:

        new_impact = impact * exp(-ln(2) * (days_since - half_life_days) / half_life_days)

    The result is clamped to [0.1, 1.0].  This is a batch operation intended
    to be called during ``trw_deliver``.

    Args:
        entries: List of learning entry dicts.  Modified **in-place**.
        half_life_days: Days before decay starts.  Defaults to config value.
    """
    cfg: TRWConfig = get_config()
    effective_half_life = half_life_days if half_life_days is not None else cfg.impact_decay_half_life_days
    now = datetime.now(timezone.utc)

    for entry in entries:
        impact = safe_float(entry, "impact", 0.5)

        # Find the best date to measure staleness from
        ref_date_str = ""
        for field in ("last_accessed_at", "last_accessed", "created"):
            raw = str(entry.get(field, ""))
            if raw and raw != "None":
                ref_date_str = raw
                break

        if not ref_date_str:
            continue

        try:
            ref_dt = _ensure_utc(datetime.fromisoformat(ref_date_str.replace("Z", "+00:00")))
        except ValueError:
            continue

        days_since = max(0, (now - ref_dt).days)

        if days_since <= effective_half_life:
            continue  # Not stale yet

        # Exponential decay: exp(-ln(2) * excess_days / half_life)
        excess = days_since - effective_half_life
        decay_factor = math.exp(-_LN2 * excess / max(effective_half_life, 1))
        new_impact = impact * decay_factor

        # Clamp to [_IMPACT_DECAY_FLOOR, 1.0]
        new_impact = max(_IMPACT_DECAY_FLOOR, min(1.0, new_impact))
        entry["impact"] = round(new_impact, 4)


__all__ = [
    "_compute_distribution_from_entries",
    "_days_since_access",
    "_entry_utility",
    "_load_entries_from_dir",
    "apply_impact_decay",
    "compute_impact_distribution",
    "enforce_tier_distribution",
]
