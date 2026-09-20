"""Response ordering for ``trw_recall`` — belongs to the ``_recall_impl.py`` facade.

Everything here reorders the ranked candidate list; nothing adds or drops a row,
so ``total_available`` and ``candidate_count`` keep their meanings. It runs
BEFORE near-duplicate collapse, the token budget and the ``max_results`` cap:
reordering after a cap cannot recover a row the cap already excluded.

PRD-CORE-282 FR01. Rows that team or company sync pulled in from another project
(``trw_mcp.state._origin_project``) have their relevance score halved before the
final order is taken. The other project-scoped surfaces use a hard partition
(``demote_unattributable``); here that partition acted as a filter, because this
project's own matches fill the default token budget on every benchmark query, so
a foreign row the judge graded relevant was never returned. The penalty keeps a
foreign row that is at least twice as relevant as the local alternatives, and
still demotes the rest. The PRD's section 1a records the measured comparison.
"""

from __future__ import annotations

import structlog

from trw_mcp.state import _origin_project
from trw_mcp.state.temporal_order import prioritize_temporal_eligibility

logger = structlog.get_logger(__name__)

#: Multiplicative penalty on a foreign row's ranked score. A fixed constant, not
#: tuned on the benchmark labels and deliberately not configurable (PRD-CORE-282
#: NFR01). Recall relevance is normalized RRF over the lexical and dense streams,
#: so 0.5 is the score of a row that tops only one of the two streams: a foreign
#: row outranks a local one only when it is a strong match in both and the local
#: row is not. Non-positive scores are pushed further down, never raised.
FOREIGN_SCORE_PENALTY = 0.5


def order_ranked_for_response(
    ranked: list[dict[str, object]],
    deprioritized_ids: set[str] | None,
) -> list[dict[str, object]]:
    """Return *ranked* in the order the caller receives it.

    Effective sort key, outermost first: temporal eligibility, already-in-context,
    attribution-adjusted score. Temporal eligibility stays outermost, so an
    ineligible local row never outranks an eligible foreign one.
    """
    ranked = _apply_foreign_penalty(ranked)
    if deprioritized_ids:
        fresh = [entry for entry in ranked if str(entry.get("id", "")) not in deprioritized_ids]
        seen = [entry for entry in ranked if str(entry.get("id", "")) in deprioritized_ids]
        ranked = fresh + seen
    return prioritize_temporal_eligibility(ranked)


def _apply_foreign_penalty(ranked: list[dict[str, object]]) -> list[dict[str, object]]:
    """Re-sort by ``combined_score`` with foreign rows penalized; fail open.

    Ties (including rows that carry no score) put this project's row first and
    otherwise keep the upstream order, so a ranker that sets no score degrades
    to the plain attribution partition the other surfaces use.
    """
    try:
        keyed = []
        for index, entry in enumerate(ranked):
            raw = entry.get("combined_score", 0.0)
            score = float(raw) if isinstance(raw, (int, float)) else 0.0
            local = _origin_project.is_attributable_to_this_project(entry)
            if not local:
                score -= FOREIGN_SCORE_PENALTY * abs(score)
            keyed.append(((score, int(local), -index), entry))
        if all(key[1] for key, _ in keyed):
            return ranked
        keyed.sort(key=lambda pair: pair[0], reverse=True)
        return [entry for _, entry in keyed]
    except Exception:  # justified: fail-open, ordering is an improvement, not a gate
        logger.debug("recall_attribution_order_failed", exc_info=True)
        return ranked
