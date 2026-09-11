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
from collections.abc import Callable
from datetime import datetime, timezone

import structlog

from trw_mcp.scoring._decay import entry_utility, utility_params_for
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
from trw_mcp.scoring._utils import get_config, safe_float

_logger = structlog.get_logger(__name__)
_level_logger = logging.getLogger(__name__)

__all__ = [
    "RecallContext",
    "infer_domains",
    "rank_by_utility",
    "utility_based_prune_candidates",
]


def rank_by_utility(
    matches: list[dict[str, object]],
    query_tokens: list[str],
    lambda_weight: float,
    assertion_penalties: dict[str, float] | Callable[[dict[str, object]], float] | None = None,
    *,
    context: RecallContext | None = None,
) -> list[dict[str, object]]:
    """Order targeted recall by query relevance, then utility/context ties.

    CORE-116 RA1–RA6 supersede the historical blended targeted formula. A
    positive lambda enables utility preferences only for equal relevance;
    lambda=0 disables those preferences. Even lambda=1 cannot ignore a query.
    Wildcards retain historical utility/context ordering. ``combined_score``
    is signed, evidence-qualified relevance for targeted recall, the legacy scalar for
    wildcards; ``preference_score`` exposes the secondary targeted score.
    Dated failure penalties and anchor invalidity qualify relevance, separately
    from positive context. No assertion verification is performed here.
    """
    if not matches:
        return matches

    today = datetime.now(tz=timezone.utc).date()
    # PRD-CORE-244 FR11: bound ONCE per pass, not per entry.
    utility_params = utility_params_for(get_config())
    scored: list[tuple[float, float, dict[str, object]]] = []
    from trw_mcp.scoring._query_relevance import query_relevance

    relevances = query_relevance(matches, query_tokens) if query_tokens else [1.0] * len(matches)
    bandit_params: dict[str, float] | None = None
    boosted_entries = 0
    intel_boosted_entries = 0
    boost_log_payload: dict[str, object] | None = None

    if context is not None and context.intel_cache is not None:
        bandit_params = context.intel_cache.get_bandit_params()

    for entry, relevance in zip(matches, relevances, strict=True):
        utility = entry_utility(entry, today, params=utility_params)
        penalty = 0.0
        if callable(assertion_penalties):
            penalty = assertion_penalties(entry)
        elif assertion_penalties:
            penalty = assertion_penalties.get(str(entry.get("id", "")), 0.0)

        # --- 6-factor multiplicative boosts (PRD-CORE-116-FR01, PRD-INFRA-053) ---
        domain_boost = 1.0
        phase_boost = 1.0
        team_boost = 1.0
        anchor_val = safe_float(entry, "anchor_validity", 1.0) if query_tokens else 1.0
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

            # 4. Anchor validity — multiplicative (not binary exclusion)
            anchor_val = safe_float(entry, "anchor_validity", 1.0)

            # 5. PRD boost (1.5x)
            if context.prd_knowledge_ids:
                eid = str(entry.get("id", ""))
                if eid in context.prd_knowledge_ids:
                    prd_boost = 1.5

            # 6. Intel boost from backend bandit params (PRD-INFRA-053)
            if bandit_params:
                entry_id = str(entry.get("id", ""))
                if entry_id in bandit_params:
                    intel_boost = max(0.5, min(2.0, float(bandit_params[entry_id])))

            if any(f != 1.0 for f in (domain_boost, phase_boost, team_boost, anchor_val, prd_boost, intel_boost)):
                boosted_entries += 1
                if intel_boost != 1.0:
                    intel_boosted_entries += 1
                if boost_log_payload is None:
                    boost_log_payload = {
                        "entry_id": str(entry.get("id", "")),
                        "domain_boost": domain_boost,
                        "phase_boost": phase_boost,
                        "team_boost": team_boost,
                        "anchor_validity": anchor_val,
                        "prd_boost": prd_boost,
                        "intel_boost": intel_boost,
                        "final_boost": round(
                            domain_boost * phase_boost * team_boost * anchor_val * prd_boost * intel_boost,
                            4,
                        ),
                    }

        positive_boost = domain_boost * phase_boost * team_boost * prd_boost * intel_boost
        if query_tokens:
            # Negative evidence qualifies primary relevance. Positive priors
            # cannot restore it or overtake a more relevant candidate.
            # Do not floor targeted scores: that would erase failure penalties
            # for lexical-zero candidates and let preferences restore their tie.
            combined = relevance * max(0.0, min(1.0, anchor_val)) - penalty
            preference = utility * positive_boost if lambda_weight > 0 else 0.0
        else:
            combined = max(0.0, (1.0 - lambda_weight) * relevance + lambda_weight * utility - penalty)
            combined = max(0.0, min(2.0, combined * positive_boost * anchor_val))
            preference = 0.0

        entry_copy = dict(entry)
        entry_copy["combined_score"] = round(combined, 4)
        if query_tokens:
            entry_copy["preference_score"] = round(preference, 4)
        scored.append((combined, preference, entry_copy))

    # ``structlog.get_logger`` may resolve to the generic BoundLogger before
    # stdlib logging is configured; that wrapper has no ``is_enabled_for``.
    # Use the stable stdlib level probe while retaining structured emission.
    if boost_log_payload is not None and _level_logger.isEnabledFor(logging.DEBUG):
        _logger.debug(
            "recall_boost_applied",
            boosted_entries=boosted_entries,
            intel_boosted_entries=intel_boosted_entries,
            matches_count=len(matches),
            **boost_log_payload,
        )

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [entry for _, _, entry in scored]
