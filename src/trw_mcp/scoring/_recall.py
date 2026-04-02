"""Recall ranking, pruning, domain inference, and contextual scoring.

PRD-FIX-010: Utility-based recall ranking and prune candidates.
PRD-CORE-102: Enhanced recall scoring with contextual boosts.

Internal module -- all public names are re-exported from ``trw_mcp.scoring``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath

import structlog

from trw_mcp.models.typed_dicts import LearningEntryDict, PruneCandidateDict
from trw_mcp.scoring._decay import _entry_utility
from trw_mcp.scoring._utils import TRWConfig, get_config, safe_float, safe_int

_logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# RecallContext (PRD-CORE-102)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecallContext:
    """Contextual information for recall scoring boosts.

    All fields are optional — when None, the corresponding boost
    defaults to 1.0 (neutral). This preserves backward compatibility.

    PRD-CORE-102 (Enhanced Recall Scoring)
    """

    current_phase: str | None = None
    active_domains: list[str] = field(default_factory=list)
    team_id: str | None = None
    active_prd_ids: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Domain inference (PRD-CORE-102)
# ---------------------------------------------------------------------------

# Structural path stems excluded from domain inference
_STRUCTURAL_STEMS: frozenset[str] = frozenset({
    "src", "lib", "test", "tests", "spec", "specs", "dist", "build",
    "node_modules", "vendor", "venv", ".venv", "__pycache__",
    "migrations", "fixtures", "mocks", "stubs", "helpers",
})


def _extract_path_stems(paths: list[str]) -> list[str]:
    """Extract meaningful directory/module stems from file paths.

    Filters out structural stems (src, test, lib, etc.) and single-char names.
    Returns unique stems in order of first appearance.
    """
    stems: list[str] = []
    seen: set[str] = set()

    for p in paths:
        parts = PurePosixPath(p).parts
        for part in parts:
            stem = part.split(".")[0].lower()  # Strip extension
            if (
                stem
                and len(stem) > 1
                and stem not in _STRUCTURAL_STEMS
                and stem not in seen
            ):
                seen.add(stem)
                stems.append(stem)

    return stems


def infer_domains(
    modified_files: list[str] | None = None,
    query: str | None = None,
) -> list[str]:
    """Infer domain labels from modified files and query text.

    Combines path-derived stems with query-derived keywords.
    Returns unique, deduplicated domain labels.

    Args:
        modified_files: Currently modified file paths.
        query: Search query text.

    Returns:
        List of unique domain label strings.
    """
    domains: list[str] = []
    seen: set[str] = set()

    if modified_files:
        for stem in _extract_path_stems(modified_files):
            if stem not in seen:
                seen.add(stem)
                domains.append(stem)

    if query:
        for token in query.lower().split():
            token = token.strip(".,;:!?()[]{}\"'")
            if (
                token
                and len(token) > 1
                and token not in _STRUCTURAL_STEMS
                and token not in seen
            ):
                seen.add(token)
                domains.append(token)

    return domains


# ---------------------------------------------------------------------------
# Recall ranking (PRD-FIX-010 + PRD-CORE-102 boosts)
# ---------------------------------------------------------------------------


def rank_by_utility(
    matches: list[dict[str, object]],
    query_tokens: list[str],
    lambda_weight: float,
    assertion_penalties: dict[str, float] | None = None,
    *,
    context: RecallContext | None = None,
) -> list[dict[str, object]]:
    """Re-rank matched learnings by combined relevance + utility score.

    Combined score = (1 - lambda) * relevance + lambda * utility

    Args:
        matches: List of matched learning entry dicts.
        query_tokens: Lowercased query tokens for relevance scoring.
        lambda_weight: Blend factor. 0.0 = pure relevance, 1.0 = pure utility.
        assertion_penalties: Optional mapping of entry ID to penalty amount
            for failing assertions (PRD-CORE-086 FR06).
        context: Optional RecallContext for contextual score boosting
            (PRD-CORE-102). When None, all boosts default to 1.0 (neutral).

    Returns:
        Sorted list (highest combined score first) with ``combined_score`` field.
    """
    if not matches:
        return matches

    today = datetime.now(tz=timezone.utc).date()
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

        # Apply assertion failure penalty (PRD-CORE-086 FR06)
        if assertion_penalties:
            entry_id = str(entry.get("id", ""))
            if entry_id in assertion_penalties:
                combined = max(0.0, combined - assertion_penalties[entry_id])

        # --- Contextual boosts (PRD-CORE-102) ---
        boost = 1.0

        if context is not None:
            # 1. Domain match boost (1.4x)
            entry_domains = entry.get("domain", [])
            if isinstance(entry_domains, list) and context.active_domains:
                if any(d in context.active_domains for d in entry_domains):
                    boost *= 1.4

            # 2. Phase match boost (1.3x)
            entry_phase_affinity = entry.get("phase_affinity", [])
            if isinstance(entry_phase_affinity, list) and context.current_phase:
                if context.current_phase.upper() in [p.upper() for p in entry_phase_affinity]:
                    boost *= 1.3

            # 3. Team match boost (1.2x)
            entry_team = str(entry.get("team_origin", ""))
            if entry_team and context.team_id and entry_team == context.team_id:
                boost *= 1.2

            # 4. Outcome strength boost (positive=1.5x, negative=0.5x)
            outcome_corr = safe_float(entry, "outcome_correlation", 0.0)
            if outcome_corr > 0.5:
                boost *= 1.5
            elif outcome_corr < -0.5:
                boost *= 0.5

            # 5. Anchor validity exclusion (0.0 validity = exclude)
            anchor_validity = safe_float(entry, "anchor_validity", 1.0)
            if anchor_validity == 0.0:
                boost = 0.0  # Exclude entirely

            if boost != 1.0:
                _logger.debug(
                    "recall_boost_applied",
                    entry_id=str(entry.get("id", "")),
                    boost=round(boost, 3),
                )

        combined *= boost

        entry_copy = dict(entry)
        entry_copy["combined_score"] = round(combined, 4)
        scored.append((combined, entry_copy))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [entry for _, entry in scored]


# --- Pruning candidate identification (PRD-FIX-010: moved from tools/learning.py) ---


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
            continue

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


__all__ = [
    "RecallContext",
    "infer_domains",
    "rank_by_utility",
    "utility_based_prune_candidates",
]
