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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config
from trw_mcp.models.run import EventType
from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = structlog.get_logger()

_config = get_config()
_reader = FileStateReader()
_writer = FileStateWriter()

# --- Field extraction helpers ---


def _float_field(entry: dict[str, object], key: str, default: float) -> float:
    """Extract a float from an entry dict, coercing through str for safety."""
    return float(str(entry.get(key, default)))


def _int_field(entry: dict[str, object], key: str, default: int) -> int:
    """Extract an int from an entry dict, coercing through str for safety."""
    return int(str(entry.get(key, default)))


def _clamp01(value: float) -> float:
    """Clamp a value to the [0.0, 1.0] range."""
    return max(0.0, min(1.0, value))


def _ensure_utc(ts: datetime) -> datetime:
    """Return a timezone-aware datetime, assuming UTC if naive."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


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
        fallback_days = _config.scoring_default_days_unused

    for field in ("last_accessed_at", "created"):
        raw = str(entry.get(field, ""))
        if not raw or raw == "None":
            continue
        try:
            return (today - date.fromisoformat(raw)).days
        except ValueError:
            continue

    return fallback_days


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
    return _clamp01(q_new)


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
    access_count: int = 0,
    source_type: str = "agent",
    access_count_boost_cap: float = 0.15,
    source_human_boost: float = 0.1,
) -> float:
    """Compute composite utility score combining Q-value with Ebbinghaus decay.

    The score determines both retrieval ranking and pruning eligibility.
    Higher scores = more valuable, less likely to be pruned, ranked higher.

    Formula:
        retention = recurrence_strength * exp(-effective_decay * days)
        effective_q = blend(impact, q_value, q_observations)
        utility = effective_q * retention + access_boost + source_boost

    PRD-CORE-026: Added access_count boost (sub-linear, capped) and
    source_type boost (+0.1 for human-sourced learnings).

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
        access_count: Number of times this learning was recalled.
        source_type: Learning provenance — 'human' or 'agent'.
        access_count_boost_cap: Maximum boost from access_count.
        source_human_boost: Utility boost for human-sourced learnings.

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

    # Base composite score
    utility = effective_q * retention

    # PRD-CORE-026-FR05: access_count boost (sub-linear, capped)
    if access_count > 0:
        utility += min(access_count_boost_cap, 0.05 * math.log1p(access_count))

    # PRD-CORE-026-FR06: source_type boost for human-sourced learnings
    if source_type == "human":
        utility += source_human_boost

    return _clamp01(utility)


def _entry_utility(
    entry: dict[str, object],
    today: date,
    fallback_days: int | None = None,
) -> float:
    """Compute utility score for a learning entry using config defaults.

    Extracts scoring fields from the entry dict and delegates to
    compute_utility_score with TRWConfig parameters.

    PRD-CORE-034: Applies time decay to base_impact before computing utility
    so that older learnings naturally sink in recall ranking results.
    """
    q_value = _float_field(entry, "q_value", _float_field(entry, "impact", 0.5))
    base_impact = _float_field(entry, "impact", 0.5)
    q_observations = _int_field(entry, "q_observations", 0)
    recurrence = _int_field(entry, "recurrence", 1)
    access_count = _int_field(entry, "access_count", 0)
    source_type = str(entry.get("source_type", "agent"))
    days_unused = _days_since_access(entry, today, fallback_days=fallback_days)

    # PRD-CORE-034: Apply time decay to both base_impact and q_value so that
    # older learnings naturally sink in recall ranking, regardless of whether
    # the cold-start threshold has been crossed.
    created_raw = str(entry.get("created", ""))
    if created_raw and created_raw != "None":
        try:
            created_dt = datetime.fromisoformat(created_raw)
            # query-time only — does not write to disk (PRD-FIX-027-FR06)
            base_impact = apply_time_decay(base_impact, created_dt)
            # query-time only — does not write to disk (PRD-FIX-027-FR06)
            q_value = apply_time_decay(q_value, created_dt)
        except ValueError:
            pass  # Fall through: use raw values if date is unparseable

    return compute_utility_score(
        q_value=q_value,
        days_since_last_access=days_unused,
        recurrence_count=recurrence,
        base_impact=base_impact,
        q_observations=q_observations,
        half_life_days=_config.learning_decay_half_life_days,
        use_exponent=_config.learning_decay_use_exponent,
        cold_start_threshold=_config.q_cold_start_threshold,
        access_count=access_count,
        source_type=source_type,
        access_count_boost_cap=_config.access_count_utility_boost_cap,
        source_human_boost=_config.source_human_utility_boost,
    )


# --- Impact distribution analysis (PRD-CORE-034) ---


def compute_impact_distribution(
    entries_dir: Path,
) -> dict[str, object]:
    """Compute the current impact score distribution across active learnings.

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
    if not entries_dir.exists():
        return {
            "total_active": 0,
            "critical": {"count": 0, "pct": 0.0},
            "high": {"count": 0, "pct": 0.0},
            "medium": {"count": 0, "pct": 0.0},
            "low": {"count": 0, "pct": 0.0},
        }

    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    total = 0

    for yaml_file in entries_dir.glob("*.yaml"):
        try:
            data = _reader.read_yaml(yaml_file)
        except Exception:  # noqa: BLE001
            continue
        if str(data.get("status", "active")) != "active":
            continue
        score = _float_field(data, "impact", 0.5)
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

    return {
        "total_active": total,
        "critical": {"count": counts["critical"], "pct": _pct(counts["critical"])},
        "high": {"count": counts["high"], "pct": _pct(counts["high"])},
        "medium": {"count": counts["medium"], "pct": _pct(counts["medium"])},
        "low": {"count": counts["low"], "pct": _pct(counts["low"])},
    }


# --- Bayesian impact score calibration (PRD-CORE-034) ---


def bayesian_calibrate(
    user_impact: float,
    org_mean: float = 0.5,
    user_weight: float = 1.0,
    org_weight: float = 0.5,
) -> float:
    """Compute Bayesian posterior impact score.

    Combines user-assigned impact with org-wide mean using weighted average.
    Higher user_weight = more trust in user's scoring accuracy.
    Higher org_weight = stronger regression toward org mean.

    Formula: (user_impact * user_weight + org_mean * org_weight) / (user_weight + org_weight)

    Args:
        user_impact: Score assigned by user (0.0-1.0)
        org_mean: Average impact across all org learnings (default 0.5)
        user_weight: User calibration accuracy weight (starts 1.0, increases with accuracy)
        org_weight: Org evidence weight (starts 0.5, caps at 2.0)

    Returns:
        Calibrated impact score (0.0-1.0)
    """
    if user_weight + org_weight == 0:
        return user_impact

    # Cap org_weight at 2.0
    org_weight = min(org_weight, 2.0)

    posterior = (user_impact * user_weight + org_mean * org_weight) / (user_weight + org_weight)
    return max(0.0, min(1.0, posterior))


def compute_calibration_accuracy(recall_stats: dict[str, object]) -> float:
    """Compute user calibration accuracy from recall tracking data.

    Users whose high-impact learnings are frequently recalled get higher accuracy.

    Returns:
        Accuracy score 0.0-2.0 (used as user_weight in bayesian_calibrate)
    """
    total = int(str(recall_stats.get("total_recalls", 0)))
    positive = int(str(recall_stats.get("positive_outcomes", 0)))

    if total == 0:
        return 1.0  # default weight

    # Positive outcome ratio → weight mapping
    # 50%+ positive → weight 1.5
    # 75%+ positive → weight 2.0
    # <25% positive → weight 0.5
    ratio = positive / total
    if ratio >= 0.75:
        return 2.0
    elif ratio >= 0.50:
        return 1.5
    elif ratio >= 0.25:
        return 1.0
    else:
        return 0.5


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
    demotion per tier per call — callers may iterate to convergence if
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
            The demotion target scores (0.89, 0.69) remain absolute —
            decay only affects which entries are classified into each tier.

    Returns:
        List of (learning_id, new_impact) tuples for entries whose scores
        were changed.  Empty list if no demotions were needed.
    """
    cfg = _config
    effective_critical_cap = critical_cap if critical_cap is not None else cfg.impact_tier_critical_cap
    effective_high_cap = high_cap if high_cap is not None else cfg.impact_tier_high_cap

    if not entries:
        return []

    total = len(entries)

    # Don't enforce distribution on very small sets — percentage caps are
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
            created_dt = datetime.fromisoformat(date_str)
            # query-time only — does not write to disk (PRD-FIX-027-FR06)
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

    # Enforce critical cap: demote lowest-scored critical → high
    if critical and len(critical) / total > effective_critical_cap:
        # Sort ascending by score to find lowest
        critical_sorted = sorted(critical, key=lambda x: x[1])
        victim_id, victim_score = critical_sorted[0]
        # Demote to top of high tier (0.89)
        new_score = round(min(0.89, max(0.7, victim_score - 0.1)), 4)
        demotions.append((victim_id, new_score))
        logger.info(
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

    # Enforce high cap: demote lowest-scored high → medium
    if effective_high_count > 0 and effective_high_count / total > effective_high_cap:
        high_sorted = sorted(
            [(lid, s) for lid, s in high if lid not in demoted_ids],
            key=lambda x: x[1],
        )
        if high_sorted:
            victim_id, victim_score = high_sorted[0]
            new_score = round(min(0.69, max(0.4, victim_score - 0.1)), 4)
            demotions.append((victim_id, new_score))
            logger.info(
                "tier_demotion",
                learning_id=victim_id,
                from_tier="high",
                to_tier="medium",
                old_score=victim_score,
                new_score=new_score,
            )

    return demotions


# --- Ebbinghaus decay for impact scores (PRD-CORE-034) ---


def apply_time_decay(impact: float, created_at: datetime) -> float:
    """Apply Ebbinghaus-inspired time decay to a raw impact score.

    The decay factor is linear (not exponential) over a 1-year window,
    floored at 0.3 to preserve minimum relevance for very old entries.

    Formula:
        days = (now - created_at).days
        decay_factor = max(0.3, 1.0 - (days / 365) * 0.3)
        effective_impact = impact * decay_factor

    Args:
        impact: Raw impact score (0.0-1.0).
        created_at: When the learning was created (timezone-aware or naive UTC).

    Returns:
        Decayed impact score in [0.0, 1.0].
    """
    now = datetime.now(timezone.utc)
    created_utc = _ensure_utc(created_at)
    days = max(0, (now - created_utc).days)
    decay_factor = max(0.3, 1.0 - (days / 365) * 0.3)
    return _clamp01(impact * decay_factor)


def apply_impact_decay(
    entries: list[dict[str, object]],
    half_life_days: int | None = None,
) -> list[dict[str, object]]:
    """Apply exponential impact decay to stale learnings (PRD-CORE-034-FR03).

    For each entry, reads ``last_accessed`` (or ``created``) date and computes
    days since that date.  If days_since exceeds ``half_life_days``, the impact
    is decayed using an exponential formula:

        new_impact = impact * exp(-0.693 * (days_since - half_life_days) / half_life_days)

    The result is clamped to [0.1, 1.0].  This is a batch operation intended
    to be called during ``trw_deliver``.

    Args:
        entries: List of learning entry dicts.  Modified in-place *and* returned.
        half_life_days: Days before decay starts.  Defaults to config value.

    Returns:
        The same list with ``impact`` fields updated where decay applied.
    """
    cfg = _config
    effective_half_life = half_life_days if half_life_days is not None else cfg.impact_decay_half_life_days
    now = datetime.now(timezone.utc)

    for entry in entries:
        impact = _float_field(entry, "impact", 0.5)

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
            ref_dt = _ensure_utc(datetime.fromisoformat(ref_date_str))
        except ValueError:
            continue

        days_since = max(0, (now - ref_dt).days)

        if days_since <= effective_half_life:
            continue  # Not stale yet

        # Exponential decay: exp(-ln(2) * excess_days / half_life)
        excess = days_since - effective_half_life
        decay_factor = math.exp(-0.693 * excess / max(effective_half_life, 1))
        new_impact = impact * decay_factor

        # Clamp to [0.1, 1.0]
        new_impact = max(0.1, min(1.0, new_impact))
        entry["impact"] = round(new_impact, 4)

    return entries


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
        raw_tags = entry.get("tags", [])
        tag_text = " ".join(str(t).lower() for t in raw_tags) if isinstance(raw_tags, list) else ""

        if query_tokens:
            summary_hits = sum(1 for t in query_tokens if t in summary)
            tag_hits = sum(1 for t in query_tokens if t in tag_text)
            detail_hits = sum(1 for t in query_tokens if t in detail)
            weighted_hits = summary_hits * 3 + tag_hits * 2 + detail_hits
            max_possible = len(query_tokens) * 3
            relevance = min(1.0, weighted_hits / max(max_possible, 1))
        else:
            relevance = 1.0  # wildcard query

        utility = _entry_utility(entry, today)

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

    for _, data in entries:
        entry_id = str(data.get("id", ""))
        if entry_id in seen_ids:
            continue

        created_str = str(data.get("created", ""))
        try:
            created = date.fromisoformat(created_str)
        except ValueError:
            continue

        age_days = (today - created).days
        recurrence = _int_field(data, "recurrence", 1)
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

        utility = _entry_utility(data, today, fallback_days=age_days)

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
                    f"recurrence={recurrence}, age={age_days}d"
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
                    f"age {age_days}d > 14d"
                ),
            })
            seen_ids.add(entry_id)

    return candidates


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
    EventType.DELIVER_COMPLETE: 1.0,  # Highest reward — delivery is the goal
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
    EventType.PHASE_CHECK: None,  # Neutral — result-specific events handle rewards
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
    EventType.CLAUDE_MD_SYNCED: 0.3,
    # Compliance
    EventType.COMPLIANCE_CHECK: None,  # Data-aware routing
}


def _find_session_start_ts(trw_dir: Path) -> datetime | None:
    """Find the timestamp of the most recent session-start event.

    Scans all events.jsonl files under docs/*/runs/*/meta/ for the most
    recent ``run_init`` or ``session_start`` event. Used for session-scoped
    correlation.

    Args:
        trw_dir: Path to .trw directory.

    Returns:
        Timestamp of the most recent session-start event, or None.
    """
    project_root = trw_dir.parent
    task_root = project_root / _config.task_root
    latest_ts: datetime | None = None

    if not task_root.exists():
        return None

    for task_dir in task_root.iterdir():
        runs_dir = task_dir / "runs"
        if not runs_dir.is_dir():
            continue
        for run_dir in sorted(runs_dir.iterdir(), reverse=True):
            events_path = run_dir / "meta" / "events.jsonl"
            if not events_path.exists():
                continue
            records = _reader.read_jsonl(events_path)
            for record in reversed(records):
                event_type = str(record.get("event", ""))
                if event_type in ("run_init", "session_start"):
                    ts_str = str(record.get("ts", ""))
                    if ts_str:
                        try:
                            ts = _ensure_utc(datetime.fromisoformat(ts_str))
                            if latest_ts is None or ts > latest_ts:
                                latest_ts = ts
                        except ValueError:
                            continue
            # Only check the most recent run
            break

    return latest_ts


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
        scope: Correlation scope — "session" or "window". Empty string
            reads from config.

    Returns:
        List of (learning_id, discount) tuples. May contain duplicates
        across receipts (caller should deduplicate).
    """
    effective_scope = scope or _config.learning_outcome_correlation_scope
    receipt_path = (
        trw_dir / _config.learnings_dir / _config.receipts_dir / "recall_log.jsonl"
    )
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

    records = _reader.read_jsonl(receipt_path)
    for record in records:
        ts_str = str(record.get("ts", ""))
        if not ts_str:
            continue
        try:
            receipt_ts = _ensure_utc(datetime.fromisoformat(ts_str))
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
            _config.scoring_recency_discount_floor,
            1.0 - elapsed_secs / total_window_secs,
        )

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
        trw_dir,
        _config.learning_outcome_correlation_window_minutes,
        scope=_config.learning_outcome_correlation_scope,
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
        q_old = _float_field(data, "q_value", _float_field(data, "impact", 0.5))
        q_obs = _int_field(data, "q_observations", 0)
        recurrence = _int_field(data, "recurrence", 1)

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

    # 2. Data-aware routing for composite events (before alias resolution,
    #    since data-aware events have None aliases as default fallback)
    if event_data:
        if event_type == EventType.TEST_RUN:
            passed = event_data.get("passed")
            if passed is True or str(passed).lower() == "true":
                return REWARD_MAP.get(EventType.TESTS_PASSED), EventType.TESTS_PASSED
            return REWARD_MAP.get(EventType.TESTS_FAILED), EventType.TESTS_FAILED
        if event_type == EventType.PRD_STATUS_CHANGE:
            new_status = str(event_data.get("new_status", "")).lower()
            if new_status == "approved":
                return REWARD_MAP.get(EventType.PRD_APPROVED), EventType.PRD_APPROVED
        if event_type == EventType.COMPLIANCE_CHECK:
            score = event_data.get("score")
            if score is not None:
                try:
                    if float(str(score)) >= 0.8:
                        return REWARD_MAP.get(EventType.COMPLIANCE_PASSED), EventType.COMPLIANCE_PASSED
                except (ValueError, TypeError):
                    pass

    # 3. EVENT_ALIASES resolution
    alias = EVENT_ALIASES.get(event_type)
    if alias is None and event_type in EVENT_ALIASES:
        # Explicit None = deliberately no reward
        return None, event_type
    if isinstance(alias, (int, float)):
        return float(alias), event_type
    if isinstance(alias, str):
        mapped_reward = REWARD_MAP.get(alias)
        if mapped_reward is not None:
            return mapped_reward, alias

    # 4. Error keyword fallback
    if any(kw in event_type.lower() for kw in _config.scoring_error_keywords):
        return _config.scoring_error_fallback_reward, event_type

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
        trw_dir = resolve_trw_dir()
        return process_outcome(trw_dir, reward, label)
    except (StateError, OSError) as exc:
        logger.debug("outcome_correlation_skipped", reason=str(exc))
        return []
