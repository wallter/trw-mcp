"""Recall ranking, pruning, domain inference, and contextual scoring.

PRD-FIX-010: Utility-based recall ranking and prune candidates.
PRD-CORE-102: Enhanced recall scoring with contextual boosts.
PRD-CORE-116: Multi-dimensional boost factors and client-aware context.

Internal module -- all public names are re-exported from ``trw_mcp.scoring``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath

import structlog

from trw_mcp.models.typed_dicts import LearningEntryDict, PruneCandidateDict
from trw_mcp.scoring._decay import _entry_utility
from trw_mcp.scoring._utils import TRWConfig, get_config, safe_float, safe_int

_logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# RecallContext (PRD-CORE-102, PRD-CORE-116)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, init=False)
class RecallContext:
    """Contextual information for recall scoring boosts.

    All fields are optional — when absent/empty, the corresponding boost
    defaults to 1.0 (neutral). This preserves backward compatibility.

    PRD-CORE-116: Extended with client_profile, model_family, inferred_domains,
    team, prd_knowledge_ids. Old field names kept as deprecated aliases.
    """

    current_phase: str | None
    inferred_domains: set[str]
    team: str
    prd_knowledge_ids: set[str]
    modified_files: list[str]
    client_profile: str
    model_family: str

    def __init__(
        self,
        *,
        current_phase: str | None = None,
        inferred_domains: set[str] | None = None,
        team: str = "",
        prd_knowledge_ids: set[str] | None = None,
        modified_files: list[str] | None = None,
        client_profile: str = "",
        model_family: str = "",
        # Deprecated aliases (backward compat)
        active_domains: list[str] | set[str] | None = None,
        team_id: str | None = None,
        active_prd_ids: list[str] | set[str] | None = None,
    ) -> None:
        # Handle deprecated aliases
        if active_domains is not None:
            _logger.warning("recall_context_deprecated_field", field="active_domains", use_instead="inferred_domains")
            if inferred_domains is None:
                inferred_domains = set(active_domains)
        if team_id is not None:
            _logger.warning("recall_context_deprecated_field", field="team_id", use_instead="team")
            if not team:
                team = team_id
        if active_prd_ids is not None:
            _logger.warning("recall_context_deprecated_field", field="active_prd_ids", use_instead="prd_knowledge_ids")
            if prd_knowledge_ids is None:
                prd_knowledge_ids = set(active_prd_ids)

        object.__setattr__(self, "current_phase", current_phase)
        object.__setattr__(self, "inferred_domains", inferred_domains if inferred_domains is not None else set())
        object.__setattr__(self, "team", team)
        object.__setattr__(self, "prd_knowledge_ids", prd_knowledge_ids if prd_knowledge_ids is not None else set())
        object.__setattr__(self, "modified_files", modified_files if modified_files is not None else [])
        object.__setattr__(self, "client_profile", client_profile)
        object.__setattr__(self, "model_family", model_family)

    @property
    def active_domains(self) -> set[str]:
        """Deprecated: use ``inferred_domains`` instead."""
        _logger.warning("recall_context_deprecated_field", field="active_domains", use_instead="inferred_domains")
        return self.inferred_domains

    @property
    def team_id(self) -> str:
        """Deprecated: use ``team`` instead."""
        _logger.warning("recall_context_deprecated_field", field="team_id", use_instead="team")
        return self.team

    @property
    def active_prd_ids(self) -> set[str]:
        """Deprecated: use ``prd_knowledge_ids`` instead."""
        _logger.warning("recall_context_deprecated_field", field="active_prd_ids", use_instead="prd_knowledge_ids")
        return self.prd_knowledge_ids


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


def _sanitize_path(p: str) -> str:
    """Sanitize a file path for domain inference.

    Strips leading '/', removes '..' traversal components, and rejects
    null bytes.  Returns a cleaned relative path string.
    """
    if "\0" in p:
        return ""
    p = p.lstrip("/")
    parts = PurePosixPath(p).parts
    return str(PurePosixPath(*[part for part in parts if part != ".."])) if parts else ""


def infer_domains(
    file_paths: list[str] | None = None,
    query: str | None = None,
    path_domain_map: dict[str, str] | None = None,
    *,
    modified_files: list[str] | None = None,
) -> set[str]:
    """Infer domain labels from file paths and query text.

    Two-stage resolution per PRD-CORE-116-FR02:
    1. Configurable prefix mapping (longest prefix wins)
    2. Directory name extraction fallback

    Args:
        file_paths: File paths to extract domains from.
        query: Search query text for keyword extraction.
        path_domain_map: Explicit prefix-to-domain mapping
            (e.g. ``{"backend/payments": "payments"}``).
        modified_files: Deprecated alias for ``file_paths``.

    Returns:
        Set of unique domain label strings.
    """
    # Handle deprecated alias
    effective_paths = file_paths
    if effective_paths is None and modified_files is not None:
        effective_paths = modified_files

    domains: set[str] = set()

    if effective_paths:
        # Sanitize paths
        sanitized = [_sanitize_path(p) for p in effective_paths]
        sanitized = [p for p in sanitized if p]

        if path_domain_map:
            # Sort prefixes by length descending for greedy matching
            sorted_prefixes = sorted(path_domain_map.keys(), key=len, reverse=True)
            # Filter out prefixes with path traversal
            safe_prefixes = []
            for pfx in sorted_prefixes:
                if ".." in pfx.split("/"):
                    _logger.warning("domain_map_traversal_dropped", prefix=pfx)
                else:
                    safe_prefixes.append(pfx)

            mapped_paths: set[int] = set()
            for i, p in enumerate(sanitized):
                for pfx in safe_prefixes:
                    if p.startswith(pfx):
                        domain_label = path_domain_map[pfx]
                        domains.add(domain_label)
                        mapped_paths.add(i)
                        _logger.debug("domain_prefix_mapped", prefix=pfx, domain=domain_label)
                        break

            # Fallback: extract stems from unmapped paths
            unmapped = [p for i, p in enumerate(sanitized) if i not in mapped_paths]
            if unmapped:
                domains.update(_extract_path_stems(unmapped))
        else:
            domains.update(_extract_path_stems(sanitized))

    if query:
        for token in query.lower().split():
            token = token.strip(".,;:!?()[]{}\"'")
            if (
                token
                and len(token) > 1
                and token not in _STRUCTURAL_STEMS
            ):
                domains.add(token)

    return domains


# ---------------------------------------------------------------------------
# Recall ranking (PRD-FIX-010 + PRD-CORE-102 boosts)
# ---------------------------------------------------------------------------


def _outcome_boost_factor(outcome_corr: float | str) -> float:
    """Map outcome_correlation to a multiplicative boost factor.

    PRD-CORE-116-FR01: Handles both string categories and float values.

    String values: "strong_positive"=1.5, "positive"=1.2,
        "neutral"=1.0, "negative"=0.5.
    Float values mapped via thresholds: >=0.75 → 1.5, >=0.5 → 1.2,
        <=-0.5 → 0.5, else → 1.0.
    """
    if isinstance(outcome_corr, str):
        return {
            "strong_positive": 1.5,
            "positive": 1.2,
            "neutral": 1.0,
            "negative": 0.5,
        }.get(outcome_corr, 1.0)

    # Float path — clamp to [-1.0, 1.0]
    val = max(-1.0, min(1.0, float(outcome_corr)))
    if val != outcome_corr:
        _logger.debug("outcome_correlation_clamped", original=outcome_corr, clamped=val)
    if val >= 0.75:
        return 1.5
    if val >= 0.5:
        return 1.2
    if val <= -0.5:
        return 0.5
    return 1.0


def rank_by_utility(
    matches: list[dict[str, object]],
    query_tokens: list[str],
    lambda_weight: float,
    assertion_penalties: dict[str, float] | None = None,
    *,
    context: RecallContext | None = None,
) -> list[dict[str, object]]:
    """Re-rank matched learnings by combined relevance + utility score.

    PRD-CORE-116: 6-factor multiplicative boost formula:
    ``combined = base * domain * phase * team * outcome * anchor * prd``

    Args:
        matches: List of matched learning entry dicts.
        query_tokens: Lowercased query tokens for relevance scoring.
        lambda_weight: Blend factor. 0.0 = pure relevance, 1.0 = pure utility.
        assertion_penalties: Optional mapping of entry ID to penalty amount
            for failing assertions (PRD-CORE-086 FR06).
        context: Optional RecallContext for contextual score boosting.
            When None, all boosts default to 1.0 (neutral).

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

        # --- 6-factor multiplicative boosts (PRD-CORE-116-FR01) ---
        domain_boost = 1.0
        phase_boost = 1.0
        team_boost = 1.0
        outcome_boost = 1.0
        anchor_val = 1.0
        prd_boost = 1.0

        if context is not None:
            # 1. Domain match boost (1.4x)
            entry_domains = entry.get("domain", [])
            if isinstance(entry_domains, list) and context.inferred_domains:
                if any(d in context.inferred_domains for d in entry_domains):
                    domain_boost = 1.4

            # 2. Phase match boost (1.3x)
            entry_phase_affinity = entry.get("phase_affinity", [])
            if isinstance(entry_phase_affinity, list) and context.current_phase:
                phase_upper = context.current_phase.upper()
                if any(p.upper() == phase_upper for p in entry_phase_affinity):
                    phase_boost = 1.3

            # 3. Team match boost (1.2x)
            entry_team = str(entry.get("team_origin", ""))
            if entry_team and context.team and entry_team == context.team:
                team_boost = 1.2

            # 4. Outcome boost (1.5/1.2/1.0/0.5)
            raw_outcome = entry.get("outcome_correlation", 0.0)
            if isinstance(raw_outcome, str):
                outcome_boost = _outcome_boost_factor(raw_outcome)
            else:
                outcome_boost = _outcome_boost_factor(safe_float(entry, "outcome_correlation", 0.0))

            # 5. Anchor validity — multiplicative (not binary exclusion)
            anchor_val = safe_float(entry, "anchor_validity", 1.0)

            # 6. PRD boost (1.5x)
            if context.prd_knowledge_ids:
                eid = str(entry.get("id", ""))
                if eid in context.prd_knowledge_ids:
                    prd_boost = 1.5

            # Log when any factor differs from 1.0
            if any(f != 1.0 for f in (domain_boost, phase_boost, team_boost, outcome_boost, anchor_val, prd_boost)):
                _logger.debug(
                    "recall_boost_applied",
                    entry_id=str(entry.get("id", "")),
                    domain_boost=domain_boost,
                    phase_boost=phase_boost,
                    team_boost=team_boost,
                    outcome_boost=outcome_boost,
                    anchor_validity=anchor_val,
                    prd_boost=prd_boost,
                    final_boost=round(domain_boost * phase_boost * team_boost * outcome_boost * anchor_val * prd_boost, 4),
                )

        combined *= domain_boost * phase_boost * team_boost * outcome_boost * anchor_val * prd_boost
        # Clamp final score
        combined = max(0.0, min(2.0, combined))

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
    "_outcome_boost_factor",
    "infer_domains",
    "rank_by_utility",
    "utility_based_prune_candidates",
]
