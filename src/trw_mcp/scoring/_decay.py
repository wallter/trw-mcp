"""Ebbinghaus decay and type-aware utility scoring.

PRD-CORE-034: Impact scoring with exponential decay.

Internal module -- all public names are re-exported from ``trw_mcp.scoring``.
Impact-tier distribution analysis and forced-distribution enforcement live in
the sibling ``_distribution.py`` deep module.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone

from trw_mcp.models.typed_dicts import LearningEntryDict
from trw_mcp.scoring._utils import (
    _IMPACT_DECAY_FLOOR,
    _LN2,
    TRWConfig,
    _ensure_utc,
    compute_utility_score,
    get_config,
    safe_float,
    safe_int,
)

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
            # Prefer datetime parsing so both date-only ("2026-06-04") and
            # full datetime ("2026-06-04T12:00:00+00:00") strings are handled.
            # date.fromisoformat raises ValueError on any string containing "T".
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return (today - parsed.date()).days
        except ValueError:
            try:
                return (today - date.fromisoformat(raw)).days
            except ValueError:
                continue

    return fallback_days


# Type-aware decay half-lives (PRD-CORE-110, PRD-CORE-116)
_TYPE_HALF_LIFE: dict[str, float] = {
    "incident": 90.0,  # Slow decay until fix confirmed (unverified = no decay, see _entry_utility)
    "pattern": 180.0,  # Very slow -- validated patterns are durable
    "convention": 9999.0,  # No auto-decay -- stable until human override
    "hypothesis": 7.0,  # Fast -- validate or die
    "workaround": 14.0,  # Fast -- scheduled expiry, typically paired with expires field
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
            # Handle both date-only ("2026-07-01") and datetime strings
            # ("2026-07-01T00:00:00+00:00"). date.fromisoformat raises ValueError
            # on strings containing "T", so try datetime first.
            parsed_expires = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
            expires_date = parsed_expires.date()
        except ValueError:
            try:
                expires_date = date.fromisoformat(expires_str)
            except ValueError:
                expires_date = None
        if expires_date is not None and today > expires_date:
            return 0.01  # Expired -> demote to very low utility

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
    "_days_since_access",
    "_entry_utility",
    "apply_impact_decay",
]
