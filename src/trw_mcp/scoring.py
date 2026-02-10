"""Utility-based scoring for the TRW self-learning layer.

Core scoring functions (compute_utility_score, update_q_value) plus
outcome correlation, recall ranking, and pruning candidate identification
extracted from tools/learning.py (PRD-FIX-010).

Research basis:
- MemRL Q-values (arXiv:2601.03192, Jan 2026)
- Ebbinghaus forgetting curve (CortexGraph, PowerMem)
- MACLA Bayesian selection (arXiv:2512.18950, Dec 2025)
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = structlog.get_logger()

_config = TRWConfig()
_reader = FileStateReader()
_writer = FileStateWriter()

# Error event classification keywords
_ERROR_KEYWORDS = ("error", "fail", "exception", "crash", "timeout")

# PRD-CORE-004: Utility-based impact scoring (Q-learning, Ebbinghaus decay)


def update_q_value(
    q_old: float,
    reward: float,
    alpha: float = 0.15,
    recurrence_bonus: float = 0.0,
) -> float:
    """Update Q-value using MemRL exponential moving average.

    Formula: Q_new = Q_old + alpha * (reward - Q_old) + recurrence_bonus

    Under stationary rewards, convergence guarantee:
    E[Q_t] = beta + (1-alpha)^t * (Q_0 - beta)
    where beta is the true expected reward.

    Args:
        q_old: Current Q-value for the learning entry (0.0-1.0).
        reward: Observed reward from outcome tracking (in [-1.0, 1.0]).
        alpha: Learning rate. Default 0.15 balances responsiveness
            with stability. Half-life of adaptation ~4.3 updates.
        recurrence_bonus: Small additive bonus when recurrence increases.
            Prevents Q-value from decaying for repeatedly-encountered issues.

    Returns:
        Updated Q-value, clamped to [0.0, 1.0].
    """
    q_new = q_old + alpha * (reward - q_old) + recurrence_bonus
    return max(0.0, min(1.0, q_new))


def compute_utility_score(
    q_value: float,
    days_since_last_access: int,
    recurrence_count: int,
    base_impact: float,
    q_observations: int,
    *,
    half_life_days: float = 14.0,
    use_exponent: float = 0.6,
    cold_start_threshold: int = 3,
) -> float:
    """Compute composite utility score combining Q-value with Ebbinghaus decay.

    The score determines both retrieval ranking and pruning eligibility.
    Higher scores = more valuable, less likely to be pruned, ranked higher.

    Formula:
        retention = recurrence_strength * exp(-effective_decay * days)
        effective_q = blend(impact, q_value, q_observations)
        utility = effective_q * retention

    Args:
        q_value: Current Q-value from outcome tracking (0.0-1.0).
        days_since_last_access: Days since last trw_recall retrieval.
            If never accessed, use days since creation.
        recurrence_count: Number of times the learning has been recalled.
            Minimum 1 (at creation).
        base_impact: Original static impact score (0.0-1.0).
        q_observations: Number of outcome observations for q_value.
        half_life_days: Days until retention halves without reinforcement.
            Default 14 (two weeks). Configurable via TRWConfig.
        use_exponent: Sub-linear exponent for recurrence count.
            Default 0.6 (from CortexGraph). Prevents over-reinforcement.
        cold_start_threshold: Number of Q-observations before fully
            trusting q_value over base_impact. Default 3.

    Returns:
        Composite utility score in [0.0, 1.0].
    """
    # Cold-start blending: transition from impact to q_value
    if q_observations < cold_start_threshold:
        w = q_observations / max(cold_start_threshold, 1)
        effective_q = (1.0 - w) * base_impact + w * q_value
    else:
        effective_q = q_value

    # Ebbinghaus decay rate from half-life: lambda = ln(2) / half_life
    decay_rate = math.log(2) / max(half_life_days, 0.1)

    # Sub-linear recurrence strength: n^beta (minimum 1)
    recurrence_strength = max(1.0, recurrence_count) ** use_exponent

    # Strength-modulated decay: higher recurrence = slower decay
    effective_decay = decay_rate / recurrence_strength
    retention = math.exp(-effective_decay * max(days_since_last_access, 0))

    # Composite score
    utility = effective_q * retention
    return max(0.0, min(1.0, utility))


# --- Recall ranking (PRD-FIX-010: moved from tools/learning.py) ---


def rank_by_utility(
    matches: list[dict[str, object]],
    query_tokens: list[str],
    lambda_weight: float,
) -> list[dict[str, object]]:
    """Re-rank matched learnings by combined relevance + utility score.

    Combined score = (1 - lambda) * relevance + lambda * utility

    Args:
        matches: List of matched learning entry dicts.
        query_tokens: Lowercased query tokens for relevance scoring.
        lambda_weight: Blend factor. 0.0 = pure relevance, 1.0 = pure utility.

    Returns:
        Sorted list (highest combined score first).
    """
    if not matches:
        return matches

    today = date.today()
    scored: list[tuple[float, dict[str, object]]] = []

    for entry in matches:
        # Text relevance score (token overlap with field weighting)
        summary = str(entry.get("summary", "")).lower()
        detail = str(entry.get("detail", "")).lower()
        entry_tags = entry.get("tags", [])
        tag_text = " ".join(
            str(t).lower() for t in entry_tags
        ) if isinstance(entry_tags, list) else ""

        if query_tokens:
            summary_hits = sum(1 for t in query_tokens if t in summary)
            tag_hits = sum(1 for t in query_tokens if t in tag_text)
            detail_hits = sum(1 for t in query_tokens if t in detail)
            weighted_hits = summary_hits * 3 + tag_hits * 2 + detail_hits * 1
            max_possible = len(query_tokens) * 3
            relevance = min(1.0, weighted_hits / max(max_possible, 1))
        else:
            relevance = 1.0  # wildcard query

        # Utility score
        q_value = float(str(entry.get("q_value", entry.get("impact", 0.5))))
        q_obs = int(str(entry.get("q_observations", 0)))
        base_impact = float(str(entry.get("impact", 0.5)))
        recurrence = int(str(entry.get("recurrence", 1)))

        last_accessed_str = str(entry.get("last_accessed_at", ""))
        created_str = str(entry.get("created", ""))
        if last_accessed_str and last_accessed_str != "None":
            try:
                last_acc = date.fromisoformat(last_accessed_str)
                days_unused = (today - last_acc).days
            except ValueError:
                days_unused = 30
        elif created_str:
            try:
                created_d = date.fromisoformat(created_str)
                days_unused = (today - created_d).days
            except ValueError:
                days_unused = 30
        else:
            days_unused = 30

        utility = compute_utility_score(
            q_value=q_value,
            days_since_last_access=days_unused,
            recurrence_count=recurrence,
            base_impact=base_impact,
            q_observations=q_obs,
            half_life_days=_config.learning_decay_half_life_days,
            use_exponent=_config.learning_decay_use_exponent,
            cold_start_threshold=_config.q_cold_start_threshold,
        )

        combined = (1.0 - lambda_weight) * relevance + lambda_weight * utility

        scored.append((combined, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [entry for _, entry in scored]


# --- Pruning candidate identification (PRD-FIX-010: moved from tools/learning.py) ---


def utility_based_prune_candidates(
    entries: list[tuple[Path, dict[str, object]]],
) -> list[dict[str, object]]:
    """Identify prune candidates using composite utility scoring.

    Three tiers:
    1. Status-based cleanup: entries already resolved/obsolete
    2. Delete candidates: utility < delete threshold (effectively forgotten)
    3. Obsolete candidates: utility < prune threshold and age > 14 days

    Backward compatible: entries without new fields use sensible defaults.

    Args:
        entries: List of (file_path, entry_data) tuples.

    Returns:
        List of candidate dicts with id, summary, utility, and suggested_status.
    """
    candidates: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    today = date.today()

    for _path, data in entries:
        entry_id = str(data.get("id", ""))
        if entry_id in seen_ids:
            continue

        created_str = str(data.get("created", ""))
        try:
            created = date.fromisoformat(created_str)
        except ValueError:
            continue

        age_days = (today - created).days
        recurrence = int(str(data.get("recurrence", 1)))
        entry_status = str(data.get("status", "active"))

        # Tier 1: Status-based cleanup (resolved/obsolete stragglers)
        if entry_status in ("resolved", "obsolete"):
            candidates.append({
                "id": entry_id,
                "summary": data.get("summary", ""),
                "age_days": age_days,
                "utility": 0.0,
                "suggested_status": entry_status,
                "reason": f"Already marked {entry_status} — cleanup candidate",
            })
            seen_ids.add(entry_id)
            continue

        # Extract scoring fields with backward-compatible defaults
        q_value = float(str(data.get("q_value", data.get("impact", 0.5))))
        q_observations = int(str(data.get("q_observations", 0)))
        base_impact = float(str(data.get("impact", 0.5)))

        last_accessed_str = str(data.get("last_accessed_at", ""))
        if last_accessed_str and last_accessed_str != "None":
            try:
                last_accessed = date.fromisoformat(last_accessed_str)
                days_since_access = (today - last_accessed).days
            except ValueError:
                days_since_access = age_days
        else:
            days_since_access = age_days

        utility = compute_utility_score(
            q_value=q_value,
            days_since_last_access=days_since_access,
            recurrence_count=recurrence,
            base_impact=base_impact,
            q_observations=q_observations,
            half_life_days=_config.learning_decay_half_life_days,
            use_exponent=_config.learning_decay_use_exponent,
            cold_start_threshold=_config.q_cold_start_threshold,
        )

        # Tier 2: Delete-level utility (effectively forgotten)
        if utility < _config.learning_utility_delete_threshold:
            candidates.append({
                "id": entry_id,
                "summary": data.get("summary", ""),
                "age_days": age_days,
                "utility": round(utility, 3),
                "suggested_status": "obsolete",
                "reason": (
                    f"Utility {utility:.3f} below delete threshold "
                    f"({_config.learning_utility_delete_threshold}). "
                    f"Q={q_value:.2f}, days_unused={days_since_access}, "
                    f"recurrence={recurrence}"
                ),
            })
            seen_ids.add(entry_id)
            continue

        # Tier 3: Prune-level utility (fading, older than 14 days)
        if utility < _config.learning_utility_prune_threshold and age_days > 14:
            candidates.append({
                "id": entry_id,
                "summary": data.get("summary", ""),
                "age_days": age_days,
                "utility": round(utility, 3),
                "suggested_status": "obsolete",
                "reason": (
                    f"Utility {utility:.3f} below prune threshold "
                    f"({_config.learning_utility_prune_threshold}) and "
                    f"age {age_days}d > 14d. Q={q_value:.2f}, "
                    f"days_unused={days_since_access}"
                ),
            })
            seen_ids.add(entry_id)

    return candidates


# --- Outcome correlation (PRD-CORE-004 Phase 1c, moved from tools/learning.py) ---

# Reward mapping: event_type -> reward signal
REWARD_MAP: dict[str, float] = {
    "tests_passed": 0.8,
    "tests_failed": -0.3,
    "task_complete": 0.5,
    "phase_gate_passed": 1.0,
    "phase_gate_failed": -0.5,
    "wave_validation_passed": 0.7,
}


def correlate_recalls(
    trw_dir: Path,
    window_minutes: int,
) -> list[tuple[str, float]]:
    """Find learning IDs from recent recall receipts within the time window.

    Returns (learning_id, recency_discount) tuples. Discount ranges from
    1.0 (just recalled) to 0.5 (at edge of window).

    Args:
        trw_dir: Path to .trw directory.
        window_minutes: How many minutes back to look for recall receipts.

    Returns:
        List of (learning_id, discount) tuples. May contain duplicates
        across receipts (caller should deduplicate).
    """
    receipt_path = (
        trw_dir / _config.learnings_dir / _config.receipts_dir / "recall_log.jsonl"
    )
    if not receipt_path.exists():
        return []

    now = datetime.now(timezone.utc)
    window_secs = window_minutes * 60
    results: list[tuple[str, float]] = []

    records = _reader.read_jsonl(receipt_path)
    for record in records:
        ts_str = str(record.get("ts", ""))
        if not ts_str:
            continue
        try:
            receipt_ts = datetime.fromisoformat(ts_str)
        except ValueError:
            continue

        # Make timezone-aware if needed
        if receipt_ts.tzinfo is None:
            receipt_ts = receipt_ts.replace(tzinfo=timezone.utc)

        elapsed_secs = (now - receipt_ts).total_seconds()
        if elapsed_secs < 0 or elapsed_secs > window_secs:
            continue

        # Recency discount: 1.0 at t=0, 0.5 at t=window
        discount = max(0.5, 1.0 - elapsed_secs / max(window_secs, 1))

        matched_ids = record.get("matched_ids", [])
        if isinstance(matched_ids, list):
            for lid in matched_ids:
                if isinstance(lid, str) and lid:
                    results.append((lid, discount))

    return results


def process_outcome(
    trw_dir: Path,
    reward: float,
    event_label: str,
) -> list[str]:
    """Update Q-values for learnings correlated with a recent outcome.

    Time-windowed correlation: only receipts from the last N minutes
    (configured via learning_outcome_correlation_window_minutes) are
    considered. Recency discount is applied to the reward.

    Args:
        trw_dir: Path to .trw directory.
        reward: Base reward signal (positive = helpful, negative = unhelpful).
        event_label: Label for outcome_history (e.g., 'tests_passed').

    Returns:
        List of learning IDs whose Q-values were updated.
    """
    from trw_mcp.state.analytics import find_entry_by_id

    correlated = correlate_recalls(
        trw_dir, _config.learning_outcome_correlation_window_minutes,
    )
    if not correlated:
        return []

    # Deduplicate — use highest discount per learning
    best_discount: dict[str, float] = {}
    for lid, discount in correlated:
        if lid not in best_discount or discount > best_discount[lid]:
            best_discount[lid] = discount

    entries_dir = trw_dir / _config.learnings_dir / _config.entries_dir
    if not entries_dir.exists():
        return []

    updated_ids: list[str] = []
    today_iso = date.today().isoformat()
    history_cap = _config.learning_outcome_history_cap

    for lid, discount in best_discount.items():
        found = find_entry_by_id(entries_dir, lid)
        if found is None:
            continue

        entry_path, data = found
        q_old = float(str(data.get("q_value", data.get("impact", 0.5))))
        q_obs = int(str(data.get("q_observations", 0)))
        recurrence = int(str(data.get("recurrence", 1)))

        # Apply recency-discounted reward
        effective_reward = reward * discount
        recurrence_bonus = _config.q_recurrence_bonus if recurrence > 1 else 0.0
        q_new = update_q_value(
            q_old, effective_reward,
            alpha=_config.q_learning_rate,
            recurrence_bonus=recurrence_bonus,
        )

        data["q_value"] = round(q_new, 4)
        data["q_observations"] = q_obs + 1
        data["updated"] = today_iso

        # Append to outcome_history (capped)
        history_entry = f"{today_iso}:{reward:+.1f}:{event_label}"
        history = data.get("outcome_history", [])
        if not isinstance(history, list):
            history = []
        history.append(history_entry)
        if len(history) > history_cap:
            history = history[-history_cap:]
        data["outcome_history"] = history

        _writer.write_yaml(entry_path, data)
        updated_ids.append(lid)

    if updated_ids:
        logger.info(
            "outcome_correlation_applied",
            reward=reward,
            event_label=event_label,
            updated_count=len(updated_ids),
        )

    return updated_ids


def process_outcome_for_event(
    event_type: str,
) -> list[str]:
    """Public entry point for orchestration tools to trigger outcome correlation.

    Checks if the event type has a known reward mapping, then correlates
    with recent recalls and updates Q-values. Best-effort: failures
    are silently caught and logged.

    Args:
        event_type: The event type string (e.g., 'tests_passed').

    Returns:
        List of learning IDs updated, or empty list if no correlation.
    """
    # Check for direct match first
    reward = REWARD_MAP.get(event_type)

    # Check for error keywords if no direct match
    if reward is None and any(kw in event_type.lower() for kw in _ERROR_KEYWORDS):
        reward = -0.3

    if reward is None:
        return []

    try:
        trw_dir = resolve_trw_dir()
        return process_outcome(trw_dir, reward, event_type)
    except (StateError, OSError) as exc:
        logger.debug("outcome_correlation_skipped", reason=str(exc))
        return []
