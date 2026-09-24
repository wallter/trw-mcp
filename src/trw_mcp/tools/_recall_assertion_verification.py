"""Stored evidence interpretation at recall; verification belongs to maintenance.

The historical internal name remains the recall facade's call boundary. No repo
scan, cache lookup, persistence, work scheduling or Q settlement occurs here.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from trw_mcp.models.config import TRWConfig
from trw_mcp.scoring._recall import RecallContext
from trw_mcp.scoring._utils import safe_float
from trw_mcp.state._recall_signals import current_recall_signals
from trw_mcp.tools._stored_claim_evidence import stored_claim_evidence


def keep_retrieval_order(
    entries: list[dict[str, object]],
    _query_tokens: list[str],
    _lambda_weight: float,
    *,
    assertion_penalties: Callable[[dict[str, object]], float],
    context: RecallContext | None = None,
) -> list[dict[str, object]]:
    """trw_recall's ranker: the retrieval pipeline's order, stale evidence demoted.

    PRD-CORE-292: re-ranking the pipeline's hybrid + cross-encoder order with a
    second, cruder lexical relevance (and utility ties from creation-seed q_values)
    dropped the gold row on 7 of 48 EngMem cells. Of that ranker's signals two are
    evidence, not preference, and stay: the dated failed-claim penalty and anchor
    invalidity (a row whose code anchors no longer resolve). A stable sort by their
    sum moves only such rows; the rest keep the order the library ranked them in.

    ``combined_score`` is the retrieval pipeline's own score, exactly as
    ``hybrid_search_scored`` produced it, which PRD-CORE-282's foreign-row penalty
    reads. It is set only when every row carries one; otherwise (keyword fallback,
    learning-ID lookups) none is set and the penalty takes its documented fallback,
    this project's rows first. Nothing is recomputed or rescaled here.
    """

    def demotion(entry: dict[str, object]) -> float:
        anchor = safe_float(entry, "anchor_validity", 1.0)
        return assertion_penalties(entry) + (1.0 - max(0.0, min(1.0, anchor)))

    ordered = sorted(entries, key=demotion)
    signals = current_recall_signals()
    scores = [signals.relevance(entry) if signals is not None else None for entry in ordered]
    if ordered and all(score is not None for score in scores):
        for entry, score in zip(ordered, scores, strict=True):
            entry["combined_score"] = score
    return ordered


def qualify_stored_evidence(
    source: dict[str, object], config: TRWConfig, moment: datetime | None = None
) -> tuple[dict[str, object], float]:
    """Copy *source* with its stored evidence as a last-known verdict; return it and its failure fraction."""
    moment = moment or datetime.now(timezone.utc)
    entry = dict(source)
    signals = current_recall_signals()
    if signals is not None:
        signals.transfer(source, entry)
    evidence, failure_fraction = stored_claim_evidence(
        source, ttl_seconds=config.verification_cache_ttl_seconds, now=moment
    )
    entry["verification_evidence"] = evidence
    state = evidence["observation"]
    entry["verification_status"] = f"last_known_{state}" if state != "unknown" else "unknown"
    # Old inline results are not present-tree proof, even on legacy dicts.
    entry.pop("assertion_status", None)
    return entry, failure_fraction


def _verify_assertions(
    ranked_learnings: list[dict[str, object]],
    query_tokens: list[str],
    config: TRWConfig,
    rank_fn: Callable[..., list[dict[str, object]]],
    context: RecallContext | None = None,
    *,
    rank_always: bool = False,
) -> list[dict[str, object]]:
    """Qualify acquired evidence and rank dated failures before downstream caps."""
    moment = datetime.now(timezone.utc)
    pairs = [qualify_stored_evidence(source, config, moment) for source in ranked_learnings]
    qualified = [entry for entry, _fraction in pairs]
    has_failures = any(fraction > 0 for _entry, fraction in pairs)
    if has_failures or rank_always:

        def candidate_penalty(entry: dict[str, object]) -> float:
            # Reuse the same UTC read instant. Never alias evidence by bare ID:
            # acquired rows may collide, including after namespace projection.
            _evidence, fraction = stored_claim_evidence(
                entry, ttl_seconds=config.verification_cache_ttl_seconds, now=moment
            )
            return config.assertion_failure_penalty * fraction

        return rank_fn(
            qualified,
            query_tokens,
            config.recall_utility_lambda,
            assertion_penalties=candidate_penalty,
            context=context,
        )
    return qualified
