"""Recall ranking, pruning, domain inference, and contextual scoring.

PRD-FIX-010: Utility-based recall ranking and prune candidates.
PRD-CORE-102: Enhanced recall scoring with contextual boosts.
PRD-CORE-116: Multi-dimensional boost factors and client-aware context.

Facade module -- all public names are re-exported from ``trw_mcp.scoring``.
Cohesive implementation lives in sibling modules and is re-exported here so
``from trw_mcp.scoring._recall import X`` keeps working:

- :mod:`trw_mcp.scoring._recall_context` -- ``RecallContext`` / cache protocol
- :mod:`trw_mcp.scoring._recall_domains` -- domain inference helpers
- :mod:`trw_mcp.scoring._recall_prune` -- prune-candidate identification
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import structlog

from trw_mcp.scoring._decay import _entry_utility
from trw_mcp.scoring._recall_context import (
    RecallContext as RecallContext,
)
from trw_mcp.scoring._recall_context import (
    _IntelCacheProtocol as _IntelCacheProtocol,
)
from trw_mcp.scoring._recall_domains import (
    _STRUCTURAL_STEMS as _STRUCTURAL_STEMS,
)
from trw_mcp.scoring._recall_domains import (
    _extract_path_stems as _extract_path_stems,
)
from trw_mcp.scoring._recall_domains import (
    _sanitize_path as _sanitize_path,
)
from trw_mcp.scoring._recall_domains import (
    infer_domains as infer_domains,
)
from trw_mcp.scoring._recall_prune import (
    utility_based_prune_candidates as utility_based_prune_candidates,
)
from trw_mcp.scoring._utils import safe_float

_logger = structlog.get_logger(__name__)

__all__ = [
    "RecallContext",
    "infer_domains",
    "rank_by_utility",
    "utility_based_prune_candidates",
]


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

    PRD-CORE-116 + PRD-INFRA-053: 7-factor multiplicative boost formula:
    ``combined = base * domain * phase * team * outcome * anchor * prd * intel``

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
    bandit_params: dict[str, float] | None = None
    boosted_entries = 0
    intel_boosted_entries = 0
    boost_log_payload: dict[str, object] | None = None

    if context is not None and context.intel_cache is not None:
        bandit_params = context.intel_cache.get_bandit_params()

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

        # --- 7-factor multiplicative boosts (PRD-CORE-116-FR01, PRD-INFRA-053) ---
        domain_boost = 1.0
        phase_boost = 1.0
        team_boost = 1.0
        outcome_boost = 1.0
        anchor_val = 1.0
        prd_boost = 1.0
        intel_boost = 1.0

        if context is not None:
            # 1. Domain match boost (1.4x)
            entry_domains = entry.get("domain", [])
            if (
                isinstance(entry_domains, list)
                and context.inferred_domains
                and any(d in context.inferred_domains for d in entry_domains)
            ):
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

            # 7. Intel boost from backend bandit params (PRD-INFRA-053)
            if bandit_params:
                entry_id = str(entry.get("id", ""))
                if entry_id in bandit_params:
                    intel_boost = max(0.5, min(2.0, float(bandit_params[entry_id])))

            if any(
                f != 1.0
                for f in (domain_boost, phase_boost, team_boost, outcome_boost, anchor_val, prd_boost, intel_boost)
            ):
                boosted_entries += 1
                if intel_boost != 1.0:
                    intel_boosted_entries += 1
                if boost_log_payload is None:
                    boost_log_payload = {
                        "entry_id": str(entry.get("id", "")),
                        "domain_boost": domain_boost,
                        "phase_boost": phase_boost,
                        "team_boost": team_boost,
                        "outcome_boost": outcome_boost,
                        "anchor_validity": anchor_val,
                        "prd_boost": prd_boost,
                        "intel_boost": intel_boost,
                        "final_boost": round(
                            domain_boost
                            * phase_boost
                            * team_boost
                            * outcome_boost
                            * anchor_val
                            * prd_boost
                            * intel_boost,
                            4,
                        ),
                    }

        combined *= domain_boost * phase_boost * team_boost * outcome_boost * anchor_val * prd_boost * intel_boost
        # Clamp final score
        combined = max(0.0, min(2.0, combined))

        entry_copy = dict(entry)
        entry_copy["combined_score"] = round(combined, 4)
        scored.append((combined, entry_copy))

    if boost_log_payload is not None and _logger.is_enabled_for(logging.DEBUG):
        _logger.debug(
            "recall_boost_applied",
            boosted_entries=boosted_entries,
            intel_boosted_entries=intel_boosted_entries,
            matches_count=len(matches),
            **boost_log_payload,
        )

    scored.sort(key=lambda x: x[0], reverse=True)
    return [entry for _, entry in scored]
