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

from trw_memory.lifecycle.scoring import (
    _clamp01 as _clamp01,
    _ensure_utc as _ensure_utc,
    apply_time_decay as apply_time_decay,
    bayesian_calibrate as bayesian_calibrate,
    compute_calibration_accuracy as compute_calibration_accuracy,
    compute_utility_score as compute_utility_score,
    update_q_value as update_q_value,
)

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.models.run import (
    ComplexityClass,
    ComplexityOverride,
    ComplexitySignals,
    EventType,
    PhaseRequirements,
)
from trw_mcp.state._helpers import safe_float, safe_int
from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = structlog.get_logger()

_config = get_config()
_reader = FileStateReader()
_writer = FileStateWriter()

# --- Scoring constants ---

_LN2: float = math.log(2)  # ≈0.693 — Ebbinghaus decay exponent
_IMPACT_DECAY_FLOOR: float = 0.1  # Minimum impact after exponential decay

# Tier boundary thresholds for enforce_tier_distribution
_TIER_HIGH_CEILING: float = 0.89  # Top of high tier (demotion target)
_TIER_MEDIUM_CEILING: float = 0.69  # Top of medium tier (demotion target)

# --- Adaptive Ceremony Depth (PRD-CORE-060) ---

# High-risk signal names used for hard override detection (FR05)
_HIGH_RISK_SIGNALS: tuple[str, ...] = (
    "security_change",
    "data_migration",
    "unknown_codebase",
)


def classify_complexity(
    signals: ComplexitySignals,
    config: TRWConfig | None = None,
) -> tuple[ComplexityClass, int, ComplexityOverride | None]:
    """Classify task complexity into MINIMAL/STANDARD/COMPREHENSIVE (FR01+FR05).

    Computes a raw score using the 6-signal point formula, then applies
    hard overrides for high-risk signals.

    Args:
        signals: The 6+3 complexity signals from the caller.
        config: Optional config override; uses singleton if None.

    Returns:
        Tuple of (tier, raw_score, override_or_None).
    """
    cfg = config or get_config()

    # FR01: Compute raw score from 6 core signals
    files_capped = min(signals.files_affected, cfg.complexity_weight_files_affected_max)
    raw_score = (
        files_capped
        + (cfg.complexity_weight_novel_patterns if signals.novel_patterns else 0)
        + (cfg.complexity_weight_cross_cutting if signals.cross_cutting else 0)
        + (cfg.complexity_weight_architecture_change if signals.architecture_change else 0)
        + (cfg.complexity_weight_external_integration if signals.external_integration else 0)
        + (cfg.complexity_weight_large_refactoring if signals.large_refactoring else 0)
    )

    # FR01: Tier assignment from raw score
    if raw_score <= cfg.complexity_tier_minimal:
        tier = ComplexityClass.MINIMAL
    elif raw_score >= cfg.complexity_tier_comprehensive + 1:
        tier = ComplexityClass.COMPREHENSIVE
    else:
        tier = ComplexityClass.STANDARD

    # FR05: Hard override for high-risk signals
    active_risk_signals = [
        name for name in _HIGH_RISK_SIGNALS
        if getattr(signals, name, False)
    ]
    override: ComplexityOverride | None = None

    if len(active_risk_signals) >= cfg.complexity_hard_override_threshold:
        # 2+ high-risk signals -> force COMPREHENSIVE
        override = ComplexityOverride(
            reason="hard override: multiple high-risk signals",
            signals=active_risk_signals,
            raw_score=raw_score,
        )
        tier = ComplexityClass.COMPREHENSIVE
    elif len(active_risk_signals) == 1 and tier == ComplexityClass.MINIMAL:
        # Single high-risk signal escalates MINIMAL -> STANDARD
        override = ComplexityOverride(
            reason="escalation: single high-risk signal prevents MINIMAL",
            signals=active_risk_signals,
            raw_score=raw_score,
        )
        tier = ComplexityClass.STANDARD

    return tier, raw_score, override


def get_phase_requirements(tier: ComplexityClass) -> PhaseRequirements:
    """Return phase mandatory/optional/skipped classification for a tier (FR04).

    IMPLEMENT and DELIVER are never in skipped regardless of tier.

    Args:
        tier: The complexity tier.

    Returns:
        PhaseRequirements model.
    """
    if tier == ComplexityClass.MINIMAL:
        return PhaseRequirements(
            mandatory=["IMPLEMENT", "DELIVER"],
            optional=[],
            skipped=["RESEARCH", "PLAN", "VALIDATE", "REVIEW"],
        )
    if tier == ComplexityClass.STANDARD:
        return PhaseRequirements(
            mandatory=["PLAN", "IMPLEMENT", "VALIDATE", "DELIVER"],
            optional=["REVIEW"],
            skipped=["RESEARCH"],
        )
    # COMPREHENSIVE
    return PhaseRequirements(
        mandatory=["RESEARCH", "PLAN", "IMPLEMENT", "VALIDATE", "REVIEW", "DELIVER"],
        optional=[],
        skipped=[],
    )


# --- Tier-Aware Ceremony Score (PRD-CORE-060-FR03) ---


class _TierExpectation:
    """Expected ceremony events and scoring rules for a complexity tier."""

    __slots__ = (
        "checkpoint_min", "events", "missing_review_penalty",
        "review_bonus", "review_mandatory",
    )

    def __init__(
        self,
        events: frozenset[str],
        checkpoint_min: int,
        review_mandatory: bool,
        review_bonus: int,
        missing_review_penalty: int,
    ) -> None:
        self.events = events
        self.checkpoint_min = checkpoint_min
        self.review_mandatory = review_mandatory
        self.review_bonus = review_bonus
        self.missing_review_penalty = missing_review_penalty


_TIER_EXPECTATIONS: dict[str, _TierExpectation] = {
    "MINIMAL": _TierExpectation(
        events=frozenset({"trw_recall", "trw_learn", "trw_deliver"}),
        checkpoint_min=0,
        review_mandatory=False,
        review_bonus=0,
        missing_review_penalty=0,
    ),
    "STANDARD": _TierExpectation(
        events=frozenset({"trw_recall", "trw_init", "trw_checkpoint", "trw_build_check", "trw_deliver"}),
        checkpoint_min=1,
        review_mandatory=False,
        review_bonus=10,
        missing_review_penalty=0,
    ),
    "COMPREHENSIVE": _TierExpectation(
        events=frozenset({
            "trw_recall", "trw_init", "trw_checkpoint",
            "trw_build_check", "trw_deliver", "trw_review",
        }),
        checkpoint_min=1,
        review_mandatory=True,
        review_bonus=0,
        missing_review_penalty=25,
    ),
}


def compute_tier_ceremony_score(
    events: list[dict[str, object]],
    complexity_class: ComplexityClass | str | None = None,
) -> dict[str, object]:
    """Compute tier-aware ceremony score (PRD-CORE-060-FR03).

    Normalizes ceremony scores against tier-appropriate phase sets and
    event expectations so that MINIMAL tasks are not penalized against
    COMPREHENSIVE baselines.

    If complexity_class is None, defaults to STANDARD behavior
    (backward compatibility).

    Args:
        events: List of event dicts from events.jsonl.
        complexity_class: The tier to score against. Accepts enum or string.

    Returns:
        Dict with score (0-100), tier used, and per-component details.
    """
    # Normalize tier to string
    if complexity_class is None:
        tier_str = "STANDARD"
    elif isinstance(complexity_class, ComplexityClass):
        tier_str = complexity_class.value
    else:
        tier_str = str(complexity_class).upper()
    if tier_str not in _TIER_EXPECTATIONS:
        tier_str = "STANDARD"

    tier_exp = _TIER_EXPECTATIONS[tier_str]

    # Detect which expected events are present
    has_recall = False
    has_init = False
    checkpoint_count = 0
    has_learn = False
    has_build_check = False
    has_deliver = False
    has_review = False

    for evt in events:
        event_type = str(evt.get("event", ""))
        tool_name = str(evt.get("tool_name", ""))
        is_tool = event_type == "tool_invocation"

        if event_type == "session_start" or (is_tool and tool_name == "trw_session_start"):
            has_recall = True
        elif event_type == "run_init" or (is_tool and tool_name == "trw_init"):
            has_init = True
        elif event_type == "checkpoint" or (is_tool and tool_name == "trw_checkpoint"):
            checkpoint_count += 1
        elif "learn" in event_type or (is_tool and tool_name == "trw_learn"):
            has_learn = True
        elif event_type == "build_check_complete" or (is_tool and tool_name == "trw_build_check"):
            has_build_check = True
        elif event_type in ("reflection_complete", "claude_md_synced", "trw_deliver_complete") or (
            is_tool and tool_name in ("trw_deliver", "trw_reflect")
        ):
            has_deliver = True
        elif event_type == "review_complete" or (is_tool and tool_name == "trw_review"):
            has_review = True

    # Score: count matched expected events proportionally
    expected = tier_exp.events
    matched = 0
    total_expected = len(expected)

    if "trw_recall" in expected and has_recall:
        matched += 1
    if "trw_init" in expected and has_init:
        matched += 1
    if "trw_checkpoint" in expected and checkpoint_count >= max(tier_exp.checkpoint_min, 1):
        matched += 1
    if "trw_learn" in expected and has_learn:
        matched += 1
    if "trw_build_check" in expected and has_build_check:
        matched += 1
    if "trw_deliver" in expected and has_deliver:
        matched += 1
    if "trw_review" in expected and has_review:
        matched += 1

    # Base score: proportion of expected events present, scaled to 100
    score = round((matched / max(total_expected, 1)) * 100)

    # Review bonus/penalty
    if has_review and tier_exp.review_bonus > 0:
        score = min(100, score + tier_exp.review_bonus)
    if tier_exp.review_mandatory and not has_review:
        score = max(0, score - tier_exp.missing_review_penalty)

    return {
        "score": score,
        "tier": tier_str,
        "matched_events": matched,
        "expected_events": total_expected,
        "has_recall": has_recall,
        "has_init": has_init,
        "checkpoint_count": checkpoint_count,
        "has_learn": has_learn,
        "has_build_check": has_build_check,
        "has_deliver": has_deliver,
        "has_review": has_review,
    }


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
    q_value = safe_float(entry, "q_value", safe_float(entry, "impact", 0.5))
    base_impact = safe_float(entry, "impact", 0.5)
    q_observations = safe_int(entry, "q_observations", 0)
    recurrence = safe_int(entry, "recurrence", 1)
    access_count = safe_int(entry, "access_count", 0)
    source_type = str(entry.get("source_type", "agent"))
    days_unused = _days_since_access(entry, today, fallback_days=fallback_days)

    # Double-decay fix (PRD-QUAL-032-FR03): apply_time_decay was removed here
    # because compute_utility_score() already applies Ebbinghaus exponential
    # decay internally via retention = exp(-decay_rate * days).

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
        except Exception:
            continue
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

    return {
        "total_active": total,
        "critical": {"count": counts["critical"], "pct": _pct(counts["critical"])},
        "high": {"count": counts["high"], "pct": _pct(counts["high"])},
        "medium": {"count": counts["medium"], "pct": _pct(counts["medium"])},
        "low": {"count": counts["low"], "pct": _pct(counts["low"])},
    }


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
        # Demote to top of high tier
        new_score = round(min(_TIER_HIGH_CEILING, max(0.7, victim_score - 0.1)), 4)
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
            new_score = round(min(_TIER_MEDIUM_CEILING, max(0.4, victim_score - 0.1)), 4)
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


def apply_impact_decay(
    entries: list[dict[str, object]],
    half_life_days: int | None = None,
) -> list[dict[str, object]]:
    """Apply exponential impact decay to stale learnings (PRD-CORE-034-FR03).

    For each entry, reads ``last_accessed`` (or ``created``) date and computes
    days since that date.  If days_since exceeds ``half_life_days``, the impact
    is decayed using an exponential formula:

        new_impact = impact * exp(-ln(2) * (days_since - half_life_days) / half_life_days)

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
            ref_dt = _ensure_utc(datetime.fromisoformat(ref_date_str))
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
        recurrence = safe_int(data, "recurrence", 1)
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

    records = _reader.read_jsonl(receipt_path)
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
                        float(str(ts_raw)), tz=timezone.utc,
                    )
                except (ValueError, OSError):
                    continue
            else:
                continue
        else:
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

        # Support both formats for learning IDs
        matched_ids = record.get("matched_ids")
        if isinstance(matched_ids, list) and matched_ids:
            for lid in matched_ids:
                if isinstance(lid, str) and lid:
                    results.append((lid, discount))
        else:
            # recall_tracking format: single learning_id
            lid_single = record.get("learning_id")
            if isinstance(lid_single, str) and lid_single:
                results.append((lid_single, discount))

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
        q_old = safe_float(data, "q_value", safe_float(data, "impact", 0.5))
        q_obs = safe_int(data, "q_observations", 0)
        recurrence = safe_int(data, "recurrence", 1)

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
