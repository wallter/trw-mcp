"""Recall ranking and pruning candidate identification.

PRD-FIX-010: Utility-based recall ranking and prune candidates.

Internal module -- all public names are re-exported from ``trw_mcp.scoring``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import trw_mcp.scoring._utils as _su
from trw_mcp.scoring._decay import _entry_utility
from trw_mcp.scoring._utils import safe_float, safe_int


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
                "reason": f"Already marked {entry_status} -- cleanup candidate",
            })
            seen_ids.add(entry_id)
            continue

        utility = _entry_utility(data, today, fallback_days=age_days)

        # Tier 2: Delete-level utility (effectively forgotten)
        if utility < _su._config.learning_utility_delete_threshold:
            candidates.append({
                "id": entry_id,
                "summary": data.get("summary", ""),
                "age_days": age_days,
                "utility": round(utility, 3),
                "suggested_status": "obsolete",
                "reason": (
                    f"Utility {utility:.3f} below delete threshold "
                    f"({_su._config.learning_utility_delete_threshold}). "
                    f"recurrence={recurrence}, age={age_days}d"
                ),
            })
            seen_ids.add(entry_id)
            continue

        # Tier 3: Prune-level utility (fading, older than 14 days)
        if utility < _su._config.learning_utility_prune_threshold and age_days > 14:
            candidates.append({
                "id": entry_id,
                "summary": data.get("summary", ""),
                "age_days": age_days,
                "utility": round(utility, 3),
                "suggested_status": "obsolete",
                "reason": (
                    f"Utility {utility:.3f} below prune threshold "
                    f"({_su._config.learning_utility_prune_threshold}) and "
                    f"age {age_days}d > 14d"
                ),
            })
            seen_ids.add(entry_id)

    return candidates


__all__ = [
    "rank_by_utility",
    "utility_based_prune_candidates",
]
