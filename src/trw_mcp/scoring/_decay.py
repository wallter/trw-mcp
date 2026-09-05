"""Ebbinghaus impact decay, plus the config adapter for entry utility.

PRD-CORE-034: Impact scoring with exponential decay.
PRD-CORE-244 FR11: the second entry-utility implementation that used to live
here is DELETED. ``entry_utility`` below binds ``TRWConfig`` to
``trw_memory.lifecycle.scoring.entry_utility``, which is now the only one.

Internal module -- all public names are re-exported from ``trw_mcp.scoring``.
Impact-tier distribution analysis and forced-distribution enforcement live in
the sibling ``_distribution.py`` deep module.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone

import trw_memory.lifecycle.scoring as _unified_scoring
from trw_memory.lifecycle._utility_params import UtilityParams

from trw_mcp.models.typed_dicts import LearningEntryDict
from trw_mcp.scoring._utils import (
    _IMPACT_DECAY_FLOOR,
    _LN2,
    TRWConfig,
    _ensure_utc,
    get_config,
    safe_float,
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


def utility_params_for(cfg: TRWConfig) -> UtilityParams:
    """Bind ``TRWConfig`` to the shared entry-utility knob bundle (FR11).

    Built ONCE per ranking pass, never per entry: this is a plain model but the
    caller's alternative — handing ``entry_utility`` a ``MemoryConfig`` — would
    read ``.trw/config.yaml`` on every row.
    """
    return UtilityParams(
        half_life_days=cfg.learning_decay_half_life_days,
        use_exponent=cfg.learning_decay_use_exponent,
        cold_start_threshold=cfg.q_cold_start_threshold,
        access_count_boost_cap=cfg.access_count_utility_boost_cap,
        source_human_boost=cfg.source_human_utility_boost,
    )


def entry_utility(
    entry: dict[str, object],
    today: date,
    fallback_days: int | None = None,
    *,
    params: UtilityParams | None = None,
) -> float:
    """Score one learning entry through the single utility implementation.

    PRD-CORE-244 FR11: this module used to carry its own ``_entry_utility``, an
    independent second implementation that read a DIFFERENT field set from
    ``trw_memory.lifecycle.scoring.entry_utility`` — notably never
    ``helpful_count``, ``unhelpful_count`` or ``recall_count``, the counters
    ``trw_learn``'s docstring credits with feeding decay. That implementation is
    deleted. What remains here is a config adapter: it binds ``TRWConfig`` knobs
    and delegates. Nothing in this package computes utility any more.
    """
    cfg: TRWConfig = get_config()
    # Module-qualified on purpose: ``from ... import entry_utility`` binds at
    # import time, so a test patching ``trw_memory.lifecycle.scoring.entry_utility``
    # would be a no-op here and the FR11 wiring proof would silently pass on a
    # tree that still had its own copy.
    return _unified_scoring.entry_utility(
        entry,
        fallback_days=fallback_days if fallback_days is not None else cfg.scoring_default_days_unused,
        params=params if params is not None else utility_params_for(cfg),
        today=today,
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
    "apply_impact_decay",
    "entry_utility",
    "utility_params_for",
]
