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

from datetime import date, datetime

import trw_memory.lifecycle.scoring as _unified_scoring
from trw_memory.lifecycle._utility_params import UtilityParams

from trw_mcp.scoring._utils import TRWConfig, get_config

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

__all__ = [
    "_days_since_access",
    "entry_utility",
    "utility_params_for",
]
