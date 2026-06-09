"""Utility-based prune-candidate identification for learning entries.

PRD-FIX-010: composite-utility prune scoring (moved from tools/learning.py).

Belongs to the ``_recall.py`` facade. Re-exported there for back-compat --
existing ``from trw_mcp.scoring import utility_based_prune_candidates`` imports
continue to work.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.models.typed_dicts import LearningEntryDict, PruneCandidateDict
from trw_mcp.scoring._decay import _entry_utility
from trw_mcp.scoring._utils import TRWConfig, get_config, safe_int

_logger = structlog.get_logger(__name__)


def utility_based_prune_candidates(
    entries: list[tuple[Path, LearningEntryDict]],
) -> list[PruneCandidateDict]:
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
    candidates: list[PruneCandidateDict] = []
    seen_ids: set[str] = set()
    today = datetime.now(tz=timezone.utc).date()
    cfg: TRWConfig = get_config()

    for _, data in entries:
        entry_id = str(data.get("id", ""))
        if entry_id in seen_ids:
            continue

        created_str = str(data.get("created", ""))
        try:
            created = date.fromisoformat(created_str.replace("Z", "+00:00"))
        except ValueError:
            # Missing/unparseable created (e.g. a YAML-migrated entry whose
            # created_at was never backfilled → "") must NOT exclude the entry
            # from prune analysis. The old `continue` skipped it BEFORE the
            # status-based Tier-1 cleanup below, so a resolved/obsolete straggler
            # with no date was immortal — never nominated for pruning. Treat it
            # as age 0 (today): conservative for the age-based tiers, while the
            # status tier still catches dead entries.
            _logger.debug("prune_candidate_undateable_created", entry_id=entry_id, created=created_str)
            created = today

        age_days = (today - created).days
        recurrence = safe_int(data, "recurrence", 1)
        entry_status = str(data.get("status", "active"))

        # Tier 1: Status-based cleanup (resolved/obsolete stragglers)
        if entry_status in ("resolved", "obsolete"):
            candidates.append(
                {
                    "id": entry_id,
                    "summary": data.get("summary", ""),
                    "age_days": age_days,
                    "utility": 0.0,
                    "suggested_status": entry_status,
                    "reason": f"Already marked {entry_status} -- cleanup candidate",
                }
            )
            seen_ids.add(entry_id)
            continue

        utility = _entry_utility(dict(data), today, fallback_days=age_days)

        # Tier 2: Delete-level utility (effectively forgotten)
        if utility < cfg.learning_utility_delete_threshold:
            candidates.append(
                {
                    "id": entry_id,
                    "summary": data.get("summary", ""),
                    "age_days": age_days,
                    "utility": round(utility, 3),
                    "suggested_status": "obsolete",
                    "reason": (
                        f"Utility {utility:.3f} below delete threshold "
                        f"({cfg.learning_utility_delete_threshold}). "
                        f"recurrence={recurrence}, age={age_days}d"
                    ),
                }
            )
            seen_ids.add(entry_id)
            continue

        # Tier 3: Prune-level utility (fading, older than 14 days)
        if utility < cfg.learning_utility_prune_threshold and age_days > 14:
            candidates.append(
                {
                    "id": entry_id,
                    "summary": data.get("summary", ""),
                    "age_days": age_days,
                    "utility": round(utility, 3),
                    "suggested_status": "obsolete",
                    "reason": (
                        f"Utility {utility:.3f} below prune threshold "
                        f"({cfg.learning_utility_prune_threshold}) and "
                        f"age {age_days}d > 14d"
                    ),
                }
            )
            seen_ids.add(entry_id)

    return candidates
