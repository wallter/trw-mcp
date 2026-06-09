"""Outcome correlation and Q-learning reward processing.

PRD-CORE-004/026: Maps events to rewards and updates Q-values for
correlated learnings.

Internal module -- all public names are re-exported from ``trw_mcp.scoring``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import trw_mcp.scoring._utils as _su
from trw_mcp.exceptions import StateError
from trw_mcp.scoring._io_boundary import (
    _batch_sync_to_sqlite,
    _PendingUpdate,
    _write_pending_entries,
)
from trw_mcp.scoring._io_boundary import (
    _default_lookup_entry as _default_lookup_entry,
)
from trw_mcp.scoring._io_boundary import (
    _find_session_start_ts as _find_session_start_ts,
)
from trw_mcp.scoring._io_boundary import (
    _sync_to_sqlite as _sync_to_sqlite,
)
from trw_mcp.scoring._recall_window import (
    _CONSECUTIVE_OLD_EARLY_EXIT as _CONSECUTIVE_OLD_EARLY_EXIT,
)
from trw_mcp.scoring._recall_window import (
    correlate_recalls as correlate_recalls,
)
from trw_mcp.scoring._reward_resolution import (
    EVENT_ALIASES,
    REWARD_MAP,
    _resolve_event_reward,
    compute_composite_outcome,
    sigmoid_normalize,
)
from trw_mcp.scoring._utils import (
    TRWConfig,
    get_config,
    safe_float,
    safe_int,
    update_q_value,
)

# Type alias for the entry lookup callable used by process_outcome.
# Given (learning_id, trw_dir, entries_dir) returns (yaml_path_or_None, data_or_None).
EntryLookupFn = Callable[[str, Path, Path], tuple[Path | None, dict[str, object] | None]]


# --- Q-value pre-seeding from impact score ---


def compute_initial_q_value(impact: float) -> float:
    """Compute the initial Q-value for a new learning entry based on its impact.

    When a learning entry has no observations (q_observations == 0), it should
    start with a Q-value that reflects its assessed impact rather than the flat
    default of 0.5.  This gives high-impact learnings an immediate advantage in
    recall ranking while still requiring outcome observations to converge to
    their true value.

    Formula: ``impact * 0.5 + 0.25``

    This blends the impact score (weight 0.5) with a neutral prior (0.5,
    weight 0.5), producing:
    - impact=0.95 -> q_value=0.725
    - impact=0.50 -> q_value=0.500 (unchanged from prior default)
    - impact=0.00 -> q_value=0.250

    The result is clamped to [0.0, 1.0] and rounded to 4 decimal places.

    Args:
        impact: The assessed impact score of the learning entry (0.0 to 1.0).

    Returns:
        Pre-seeded Q-value, clamped to [0.0, 1.0], rounded to 4 decimals.
    """
    return round(max(0.0, min(1.0, impact * 0.5 + 0.25)), 4)


# --- Outcome correlation (PRD-CORE-004 Phase 1c, moved from tools/learning.py) ---


# Backward-compat alias (tests may patch this name directly)
_lookup_learning_entry = _default_lookup_entry


def _deduplicate_recalls(
    correlated: list[tuple[str, float]],
) -> dict[str, float]:
    """Keep highest discount per learning ID."""
    best_discount: dict[str, float] = {}
    for lid, discount in correlated:
        if lid not in best_discount or discount > best_discount[lid]:
            best_discount[lid] = discount
    return best_discount


def _update_entry_q_values(
    data: dict[str, object],
    reward: float,
    discount: float,
    cfg: TRWConfig,
) -> tuple[float, dict[str, object]]:
    """Compute new Q-value and update entry data dict.

    Returns (q_new, updated_data).
    """
    q_old = safe_float(data, "q_value", safe_float(data, "impact", 0.5))
    q_obs = safe_int(data, "q_observations", 0)
    recurrence = safe_int(data, "recurrence", 1)

    effective_reward = reward * discount
    recurrence_bonus = cfg.q_recurrence_bonus if recurrence > 1 else 0.0
    q_new = update_q_value(
        q_old,
        effective_reward,
        alpha=cfg.q_learning_rate,
        recurrence_bonus=recurrence_bonus,
    )

    today_iso = datetime.now(tz=timezone.utc).date().isoformat()
    data["q_value"] = round(q_new, 4)
    data["q_observations"] = q_obs + 1
    data["updated"] = today_iso

    return q_new, data


def _update_entry_history(
    data: dict[str, object],
    reward: float,
    event_label: str,
    history_cap: int,
) -> dict[str, object]:
    """Append outcome_history entry (capped)."""
    today_iso = datetime.now(tz=timezone.utc).date().isoformat()
    history_entry = f"{today_iso}:{reward:+.1f}:{event_label}"
    history = data.get("outcome_history", [])
    if not isinstance(history, list):
        history = []
    history.append(history_entry)
    if len(history) > history_cap:
        history = history[-history_cap:]
    data["outcome_history"] = history
    return data


def process_outcome(
    trw_dir: Path,
    reward: float,
    event_label: str,
    *,
    lookup_fn: EntryLookupFn | None = None,
    tool_call_id: str | None = None,
) -> list[str]:
    """Update Q-values for learnings correlated with a recent outcome.

    PRD-FIX-053-FR03: Uses SQLite (O(1)) lookup via memory_adapter.find_entry_by_id()
    instead of scanning all YAML files. Falls back to analytics.find_entry_by_id()
    (YAML glob scan) when SQLite returns None for pre-migration entries.

    PRD-FIX-061-FR05: Backend selection is now at the call boundary via
    ``lookup_fn``.  Defaults to ``_default_lookup_entry`` which uses
    SQLite-primary, YAML-fallback.

    Time-windowed correlation: only receipts from the last N minutes
    (configured via learning_outcome_correlation_window_minutes) are
    considered. Recency discount is applied to the reward.

    Args:
        trw_dir: Path to .trw directory.
        reward: Base reward signal (positive = helpful, negative = unhelpful).
        event_label: Label for outcome_history (e.g., 'tests_passed').
        lookup_fn: Optional callable for entry lookup. Signature:
            ``(lid, trw_dir, entries_dir) -> (path_or_None, data_or_None)``.
            Defaults to ``_default_lookup_entry`` (SQLite + YAML fallback).

    Returns:
        List of learning IDs whose Q-values were updated.
    """
    cfg: TRWConfig = get_config()
    correlated = correlate_recalls(
        trw_dir,
        cfg.learning_outcome_correlation_window_minutes,
        scope=cfg.learning_outcome_correlation_scope,
    )
    if not correlated:
        return []

    effective_lookup = lookup_fn if lookup_fn is not None else _default_lookup_entry
    best_discount = _deduplicate_recalls(correlated)
    entries_dir = trw_dir / cfg.learnings_dir / cfg.entries_dir

    # Phase 1: Compute all Q-values (no I/O writes) -- PRD-FIX-070-FR04
    pending_updates: list[_PendingUpdate] = []
    for lid, discount in best_discount.items():
        entry_path, data = effective_lookup(lid, trw_dir, entries_dir)
        if data is None:
            continue
        q_new, data = _update_entry_q_values(data, reward, discount, cfg)
        data = _update_entry_history(data, reward, event_label, cfg.learning_outcome_history_cap)
        q_obs = safe_int(data, "q_observations", 0)
        history = data.get("outcome_history", [])
        if not isinstance(history, list):
            history = []
        pending_updates.append((lid, entry_path, data, q_new, q_obs, history))

    # Phase 2: Batch YAML writes (PRD-FIX-070-FR04)
    updated_ids = _write_pending_entries(pending_updates)

    # Phase 3: Batch SQLite syncs (PRD-FIX-070-FR03)
    _batch_sync_to_sqlite(pending_updates, trw_dir)

    if updated_ids:
        # PRD-FIX-088 FR01: stamp ``tool_call_id`` so async correlation
        # events can be threaded back to the originating ``trw_build_check``
        # call (or other dispatcher).  ``None`` is omitted via kwargs
        # filtering so callers that don't supply one don't pollute logs.
        log_kwargs: dict[str, object] = {
            "reward": reward,
            "event_label": event_label,
            "updated_count": len(updated_ids),
        }
        if tool_call_id is not None:
            log_kwargs["tool_call_id"] = tool_call_id
        _su.logger.info("outcome_correlation_applied", **log_kwargs)

    return updated_ids


def process_outcome_for_event(
    event_type: str,
    event_data: dict[str, object] | None = None,
    *,
    tool_call_id: str | None = None,
) -> list[str]:
    """Public entry point for orchestration tools to trigger outcome correlation.

    PRD-CORE-026-FR03: Resolves aliases before REWARD_MAP lookup, accepts
    optional event_data for data-aware routing (e.g., test_run with
    passed=true routes to tests_passed reward).

    Args:
        event_type: The event type string (e.g., 'tests_passed').
        event_data: Optional event data dict for data-aware routing.

    Returns:
        List of learning IDs updated, or empty list if no correlation.
    """
    reward, label = _resolve_event_reward(event_type, event_data)

    if reward is None:
        return []

    try:
        trw_dir = _su.resolve_trw_dir()
        return process_outcome(trw_dir, reward, label, tool_call_id=tool_call_id)
    except (StateError, OSError) as exc:
        _su.logger.debug("outcome_correlation_skipped", reason=str(exc))
        return []


__all__ = [
    "EVENT_ALIASES",
    "REWARD_MAP",
    "_find_session_start_ts",
    "_resolve_event_reward",
    "compute_composite_outcome",
    "compute_initial_q_value",
    "correlate_recalls",
    "process_outcome",
    "process_outcome_for_event",
    "sigmoid_normalize",
]
