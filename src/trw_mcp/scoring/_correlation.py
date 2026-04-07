"""Outcome correlation and Q-learning reward processing.

PRD-CORE-004/026: Maps events to rewards and updates Q-values for
correlated learnings.

Internal module -- all public names are re-exported from ``trw_mcp.scoring``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog

import trw_mcp.scoring._utils as _su
from trw_mcp.exceptions import StateError
from trw_mcp.models.run import EventType
from trw_mcp.scoring._io_boundary import (
    _batch_sync_to_sqlite,
    _PendingUpdate,
    _read_recall_tracking_jsonl,
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
from trw_mcp.scoring._utils import (
    TRWConfig,
    _ensure_utc,
    get_config,
    safe_float,
    safe_int,
    update_q_value,
)

# Type alias for the entry lookup callable used by process_outcome.
# Given (learning_id, trw_dir, entries_dir) returns (yaml_path_or_None, data_or_None).
EntryLookupFn = Callable[[str, Path, Path], tuple[Path | None, dict[str, object] | None]]

logger = structlog.get_logger(__name__)


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

# Reward mapping: EventType -> reward signal
# PRD-CORE-026: Expanded from 6 to 12 entries
# Sprint 8: Migrated from magic strings to EventType enum
REWARD_MAP: dict[str, float] = {
    EventType.TESTS_PASSED: 0.8,
    EventType.TESTS_FAILED: -0.3,
    EventType.TASK_COMPLETE: 0.5,
    EventType.PHASE_GATE_PASSED: 1.0,
    EventType.PHASE_GATE_FAILED: -0.5,
    EventType.WAVE_VALIDATION_PASSED: 0.7,
    EventType.SHARD_COMPLETE: 0.6,
    EventType.REFLECTION_COMPLETE: 0.4,
    EventType.COMPLIANCE_PASSED: 0.5,
    EventType.FILE_MODIFIED: 0.2,
    EventType.PRD_APPROVED: 0.7,
    EventType.WAVE_COMPLETE: 0.8,
    EventType.DELIVER_COMPLETE: 1.0,  # Highest reward -- delivery is the goal
    EventType.BUILD_PASSED: 0.6,
    EventType.BUILD_FAILED: -0.4,
}

# PRD-CORE-026: Alias mapping for internal event types that don't match
# REWARD_MAP keys directly. Maps event_type -> REWARD_MAP key or direct
# float reward. None values are explicitly ignored (no reward).
# Sprint 8: Migrated from magic strings to EventType enum
EVENT_ALIASES: dict[str, str | float | None] = {
    # Wave/shard lifecycle
    EventType.SHARD_COMPLETED: EventType.SHARD_COMPLETE,
    EventType.SHARD_STARTED: None,  # No reward for starting
    EventType.WAVE_VALIDATED: EventType.WAVE_VALIDATION_PASSED,
    EventType.WAVE_COMPLETED: EventType.WAVE_COMPLETE,
    # Phase lifecycle
    EventType.PHASE_CHECK: None,  # Neutral -- result-specific events handle rewards
    EventType.PHASE_ENTER: None,
    EventType.PHASE_REVERT: -0.3,
    # Run lifecycle
    EventType.RUN_INIT: None,
    EventType.RUN_RESUMED: None,
    EventType.SESSION_START: None,
    # PRD lifecycle
    EventType.PRD_STATUS_CHANGE: None,  # Handled by data-aware routing below
    EventType.PRD_CREATED: 0.3,
    # Testing
    EventType.TEST_RUN: None,  # Data-aware: routed by passed/failed in event_data
    # Checkpoint/reflection
    EventType.CHECKPOINT: 0.1,
    EventType.REFLECTION_COMPLETED: EventType.REFLECTION_COMPLETE,
    # Compliance
    EventType.COMPLIANCE_CHECK: None,  # Data-aware routing
}


# _find_session_start_ts re-exported from _io_boundary (PRD-FIX-061-FR05).


def _extract_recalled_ids(record: dict[str, object]) -> list[str]:
    """Return learning IDs only for actual recall receipts.

    ``recall_tracking.jsonl`` mixes recall events and later outcome-only rows.
    Outcome rows must not be treated as fresh recall evidence for correlation.
    """
    matched_ids = record.get("matched_ids")
    if isinstance(matched_ids, list) and matched_ids:
        return [lid for lid in matched_ids if isinstance(lid, str) and lid]

    lid_single = record.get("learning_id")
    if not isinstance(lid_single, str) or not lid_single:
        return []

    outcome = record.get("outcome")
    if outcome not in (None, ""):
        return []

    return [lid_single]


def correlate_recalls(
    trw_dir: Path,
    window_minutes: int,
    *,
    scope: str = "",
) -> list[tuple[str, float]]:
    """Find learning IDs from recent recall receipts within the correlation scope.

    PRD-CORE-026-FR04: Session-scoped correlation replaces the fixed 30-min
    window. When scope="session", correlates with ALL recall receipts since
    the last run_init/session_start event. Falls back to window-based when
    no session boundary is found.

    Returns (learning_id, recency_discount) tuples. Discount ranges from
    1.0 (just recalled) to 0.5 (at edge of window).

    Args:
        trw_dir: Path to .trw directory.
        window_minutes: How many minutes back to look for recall receipts
            (used when scope is "window" or as fallback).
        scope: Correlation scope -- "session" or "window". Empty string
            reads from config.

    Returns:
        List of (learning_id, discount) tuples. May contain duplicates
        across receipts (caller should deduplicate).
    """
    cfg_corr: TRWConfig = get_config()
    effective_scope = scope or cfg_corr.learning_outcome_correlation_scope
    receipt_path = trw_dir / "logs" / "recall_tracking.jsonl"
    if not receipt_path.exists():
        return []

    now = datetime.now(timezone.utc)

    # Determine the cutoff timestamp based on scope (session overrides window)
    cutoff_ts = now - timedelta(minutes=window_minutes)
    if effective_scope == "session":
        session_start = _find_session_start_ts(trw_dir)
        if session_start is not None:
            cutoff_ts = session_start

    # Total seconds from cutoff to now (for discount calculation)
    total_window_secs = max((now - cutoff_ts).total_seconds(), 1.0)
    results: list[tuple[str, float]] = []

    # PRD-FIX-061-FR05: file I/O delegated to _io_boundary
    records = _read_recall_tracking_jsonl(receipt_path)
    for record in records:
        # Support both receipt formats:
        # - Legacy receipts: {"ts": ISO string, "matched_ids": [...]}
        # - recall_tracking: {"timestamp": unix float, "learning_id": str}
        ts_str = str(record.get("ts", ""))
        if not ts_str:
            # Try recall_tracking format: unix timestamp float
            ts_raw = record.get("timestamp")
            if ts_raw is not None:
                try:
                    receipt_ts = datetime.fromtimestamp(
                        float(str(ts_raw)),
                        tz=timezone.utc,
                    )
                except (ValueError, OSError):
                    continue
            else:
                continue
        else:
            try:
                receipt_ts = _ensure_utc(datetime.fromisoformat(ts_str.replace("Z", "+00:00")))
            except ValueError:
                continue

        # Skip receipts outside the correlation scope
        if receipt_ts < cutoff_ts:
            continue
        elapsed_secs = (now - receipt_ts).total_seconds()
        if elapsed_secs < 0:
            continue

        # Recency discount: 1.0 at t=0, floor at t=window_edge
        discount = max(
            cfg_corr.scoring_recency_discount_floor,
            1.0 - elapsed_secs / total_window_secs,
        )

        recalled_ids = _extract_recalled_ids(record)
        if recalled_ids:
            results.extend((lid, discount) for lid in recalled_ids)

    return results


def _deduplicate_recalls(
    correlated: list[tuple[str, float]],
) -> dict[str, float]:
    """Keep highest discount per learning ID."""
    best_discount: dict[str, float] = {}
    for lid, discount in correlated:
        if lid not in best_discount or discount > best_discount[lid]:
            best_discount[lid] = discount
    return best_discount


# _default_lookup_entry re-exported from _io_boundary (PRD-FIX-061-FR05).
# Backward-compat alias (tests may patch this name directly)
_lookup_learning_entry = _default_lookup_entry


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


# _sync_to_sqlite re-exported from _io_boundary (PRD-FIX-061-FR05).
# _PendingUpdate type alias re-exported from _io_boundary for backward compat.


def process_outcome(
    trw_dir: Path,
    reward: float,
    event_label: str,
    *,
    lookup_fn: EntryLookupFn | None = None,
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
        data = _update_entry_history(
            data, reward, event_label, cfg.learning_outcome_history_cap
        )
        q_obs = safe_int(data, "q_observations", 0)
        history = data.get("outcome_history", [])
        if not isinstance(history, list):
            history = []
        pending_updates.append((lid, entry_path, data, q_new, q_obs, history))

    # Phase 2: Batch YAML writes -- PRD-FIX-070-FR04 / PRD-FIX-061-FR05
    updated_ids = _write_pending_entries(pending_updates)

    # Phase 3: Batch SQLite syncs -- PRD-FIX-070-FR03
    _batch_sync_to_sqlite(pending_updates, trw_dir)

    if updated_ids:
        _su.logger.info(
            "outcome_correlation_applied",
            reward=reward,
            event_label=event_label,
            updated_count=len(updated_ids),
        )

    return updated_ids


def _resolve_test_run_reward(
    event_data: dict[str, object],
) -> tuple[float | None, str]:
    """Resolve test_run event to tests_passed or tests_failed."""
    passed = event_data.get("passed")
    if passed is True or str(passed).lower() == "true":
        return REWARD_MAP.get(EventType.TESTS_PASSED), EventType.TESTS_PASSED
    return REWARD_MAP.get(EventType.TESTS_FAILED), EventType.TESTS_FAILED


def _resolve_prd_status_change_reward(
    event_data: dict[str, object],
) -> tuple[float | None, str]:
    """Resolve prd_status_change event."""
    new_status = str(event_data.get("new_status", "")).lower()
    if new_status == "approved":
        return REWARD_MAP.get(EventType.PRD_APPROVED), EventType.PRD_APPROVED
    return None, EventType.PRD_STATUS_CHANGE


def _resolve_compliance_check_reward(
    event_data: dict[str, object],
) -> tuple[float | None, str]:
    """Resolve compliance_check event based on score."""
    score = event_data.get("score")
    if score is not None:
        try:
            if float(str(score)) >= 0.8:
                return REWARD_MAP.get(EventType.COMPLIANCE_PASSED), EventType.COMPLIANCE_PASSED
        except (ValueError, TypeError):
            logger.debug("compliance_score_parse_failed", exc_info=True)
    return None, EventType.COMPLIANCE_CHECK


def _resolve_data_aware_routing(
    event_type: str,
    event_data: dict[str, object],
) -> tuple[float | None, str] | None:
    """Try data-aware routing for composite events.

    Returns (reward, label) tuple if matched, None otherwise.
    """
    if event_type == EventType.TEST_RUN:
        return _resolve_test_run_reward(event_data)
    if event_type == EventType.PRD_STATUS_CHANGE:
        return _resolve_prd_status_change_reward(event_data)
    if event_type == EventType.COMPLIANCE_CHECK:
        return _resolve_compliance_check_reward(event_data)
    return None


def _resolve_alias_reward(event_type: str) -> tuple[float | None, str] | None:
    """Resolve via EVENT_ALIASES.

    Returns (reward, label) tuple if matched, None otherwise.
    """
    alias = EVENT_ALIASES.get(event_type)
    if alias is None and event_type in EVENT_ALIASES:
        return None, event_type
    if isinstance(alias, (int, float)):
        return float(alias), event_type
    if isinstance(alias, str):
        mapped_reward = REWARD_MAP.get(alias)
        if mapped_reward is not None:
            return mapped_reward, alias
    return None


def _resolve_event_reward(
    event_type: str,
    event_data: dict[str, object] | None = None,
) -> tuple[float | None, str]:
    """Resolve an event type to a reward value and canonical label.

    PRD-CORE-026-FR01/FR03: Resolution order:
    1. Direct REWARD_MAP match
    2. Data-aware routing (e.g., test_run + passed=true -> tests_passed)
    3. EVENT_ALIASES -> REWARD_MAP key or direct float
    4. Error keyword fallback

    Args:
        event_type: The event type string (e.g., 'shard_completed').
        event_data: Optional event data dict for data-aware routing.

    Returns:
        Tuple of (reward_value_or_None, canonical_label).
    """
    # 1. Direct REWARD_MAP match
    reward = REWARD_MAP.get(event_type)
    if reward is not None:
        return reward, event_type

    # 2. Data-aware routing for composite events
    if event_data:
        result = _resolve_data_aware_routing(event_type, event_data)
        if result is not None:
            return result

    # 3. EVENT_ALIASES resolution
    result = _resolve_alias_reward(event_type)
    if result is not None:
        return result

    # 4. Error keyword fallback
    cfg_err: TRWConfig = get_config()
    if any(kw in event_type.lower() for kw in cfg_err.scoring_error_keywords):
        return cfg_err.scoring_error_fallback_reward, event_type

    return None, event_type


def process_outcome_for_event(
    event_type: str,
    event_data: dict[str, object] | None = None,
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
        return process_outcome(trw_dir, reward, label)
    except (StateError, OSError) as exc:
        _su.logger.debug("outcome_correlation_skipped", reason=str(exc))
        return []


def compute_composite_outcome(
    *,
    rework_rate: float = 0.0,
    p0_defect_count: int = 0,
    velocity_tasks: float = 0.0,
    learning_rate: float = 0.0,
    weight_rework: float = -2.0,
    weight_p0_defects: float = -1.5,
    weight_velocity: float = 0.5,
    weight_learning_rate: float = 0.3,
) -> float:
    """Compute composite outcome score respecting TRW value hierarchy.

    Formula: w_rework * rework + w_p0 * p0_count + w_velocity * velocity + w_lr * learning_rate
    Quality penalties outweigh velocity rewards (Truthfulness > Quality > Velocity).

    PRD-CORE-104-FR02.
    """
    return (
        weight_rework * rework_rate
        + weight_p0_defects * p0_defect_count
        + weight_velocity * velocity_tasks
        + weight_learning_rate * learning_rate
    )


def sigmoid_normalize(score: float, steepness: float = 1.0) -> float:
    """Map composite outcome score to [0, 1] via sigmoid.

    sigmoid(0) = 0.5, negative -> <0.5, positive -> >0.5.
    PRD-CORE-104-FR05.
    """
    import math

    return 1.0 / (1.0 + math.exp(-steepness * score))


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
