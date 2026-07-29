"""Named factories for ``recall_learnings`` callers — PRD-FIX-085 FR05.

Pre-fix, 10+ call sites used ``recall_learnings(...)`` with divergent
parameter combinations (varying compact, min_impact, max_results, tags,
status). Each call site was its own bug surface; subtle parameter drift
across call sites made refactors brittle.

Post-fix, callers use one of the named factories below. Each factory
encodes the actual usage pattern (with the constants pinned) so the
caller declares INTENT instead of assembling ad-hoc parameters.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


def _default_recall() -> Callable[..., list[dict[str, object]]]:
    """Lazy import to avoid a cycle on package init."""
    from trw_mcp.state.memory_adapter import recall_learnings

    return recall_learnings


# ---------------------------------------------------------------------------
# Session-start factories
# ---------------------------------------------------------------------------


def recall_baseline_high_impact(
    trw_dir: Path,
    *,
    max_results: int,
    allow_cold_embedding_init: bool = False,
) -> list[dict[str, object]]:
    """Wildcard recall of high-impact learnings.

    Used by the session_start baseline path -- pulls universally-relevant
    "tribal knowledge" entries to surface at the start of every session.
    Compact mode by default; only ``id``/``summary``/``tags``/``impact``
    are needed for the typical caller.
    """
    return _default_recall()(
        trw_dir,
        query="*",
        min_impact=0.7,
        max_results=max_results,
        compact=True,
        allow_cold_embedding_init=allow_cold_embedding_init,
        status="active",  # exclude obsolete/archived — the wildcard path has no implicit status filter
    )


def recall_focused(
    trw_dir: Path,
    query: str,
    *,
    max_results: int,
    min_impact: float = 0.3,
    allow_cold_embedding_init: bool = False,
) -> list[dict[str, object]]:
    """Focused recall on a user-supplied query.

    Used by the session_start focused path. ``allow_cold_embedding_init=False``
    means this factory never triggers a model load, so it reaches BM25 + vector
    hybrid search ONLY when some earlier operation in the same process already
    initialized the embedder. When the embedder is uninitialized the search
    degrades to all-token keyword matching, which a multi-word natural-language
    query cannot satisfy — see :func:`focused_recall_zero_match_advisory`, which
    explains a zero-row result to the caller. Compact mode by default.
    """
    return _default_recall()(
        trw_dir,
        query=query,
        min_impact=min_impact,
        max_results=max_results,
        compact=True,
        allow_cold_embedding_init=allow_cold_embedding_init,
        status="active",  # focused recall must not surface obsolete/archived learnings
    )


def recall_recent_bypass(
    trw_dir: Path,
    *,
    max_results: int,
    min_impact: float,
    allow_cold_embedding_init: bool = False,
) -> list[dict[str, object]]:
    """Pull recently-stored learnings that the high-impact baseline filters out.

    Session_start L-fovv fix: low-impact entries from the current/recent
    session would otherwise be invisible at the next session_start because
    the baseline filters at min_impact=0.7. This factory uses min_impact
    from config and returns full entries so the caller can date-filter.
    """
    return _default_recall()(
        trw_dir,
        query="*",
        min_impact=min_impact,
        max_results=max_results,
        compact=False,
        allow_cold_embedding_init=allow_cold_embedding_init,
        status="active",  # recent-bypass must not prepend obsolete entries at top priority
    )


# ---------------------------------------------------------------------------
# Zero-match advisory for the focused session-start path
# ---------------------------------------------------------------------------

# Both strings are emitted ONLY when a non-wildcard focused recall returns zero
# rows, so they cost nothing on the normal path (token-budget rule: advisory
# fields are omitted when they carry no signal). Without them, ``query_matched:
# 0`` is unexplained and the caller reads the impact-ranked baseline union as if
# it were query hits.
_UNINITIALIZED_INDEX_ADVISORY = (
    "Focused recall matched 0 entries: the vector index was not initialized in this "
    "process (session_start never triggers a model load), so only all-token keyword "
    "matching ran -- a multi-word natural-language query cannot match that way. The "
    "learnings returned are the impact-ranked baseline, NOT query matches. Call "
    "trw_recall(query=...) for full hybrid BM25+vector search."
)

_HYBRID_INDEX_ADVISORY = (
    "Focused recall matched 0 entries via hybrid search. The learnings returned are the "
    "impact-ranked baseline, NOT query matches. Broaden the query or call "
    "trw_recall(query=..., min_impact=0)."
)


def focused_recall_zero_match_advisory() -> str:
    """Explain a zero-row :func:`recall_focused` result to the calling agent.

    Probes the embedder cache WITHOUT initializing it (``get_initialized_embedder``
    is the same non-loading accessor the recall path itself uses), so the advisory
    reports what actually ran. Must be called at recall time: later session_start
    steps may initialize the embedder, which would make a deferred probe lie.

    Fail-open: an import/probe failure reports the uninitialized-index wording,
    which is the conservative reading (it tells the caller to re-run via
    ``trw_recall``).
    """
    try:
        from trw_mcp.state._memory_connection import get_initialized_embedder

        initialized = get_initialized_embedder() is not None
    except Exception:  # justified: advisory text must never break session start
        logger.debug("focused_recall_advisory_probe_failed", exc_info=True)
        initialized = False
    return _HYBRID_INDEX_ADVISORY if initialized else _UNINITIALIZED_INDEX_ADVISORY


# ---------------------------------------------------------------------------
# Nudge factories
# ---------------------------------------------------------------------------


def recall_for_nudge_pool(
    trw_dir: Path,
    *,
    query: str = "*",
    tags: list[str] | None = None,
    min_impact: float = 0.5,
    max_results: int = 10,
) -> list[dict[str, object]]:
    """Recall candidates for nudge content selection.

    Used by ``_try_learning_nudge_content`` and ``select_contextual_nudge_content``
    pools. ``compact=False`` because nudge text rendering needs the
    learning's ``summary`` and possibly ``detail``.
    """
    return _default_recall()(
        trw_dir,
        query=query,
        tags=tags,
        min_impact=min_impact,
        max_results=max_results,
        compact=False,
        status="active",  # nudges must not be sourced from obsolete/archived learnings
    )


# ---------------------------------------------------------------------------
# Review / publish factories
# ---------------------------------------------------------------------------


def recall_for_review_tags(
    trw_dir: Path,
    *,
    tags: list[str],
    min_impact: float,
    max_results: int,
) -> list[dict[str, object]]:
    """Tag-scoped recall of active learnings.

    Used by ``state/claude_md`` review/publish flow. Filters on a fixed
    tag set, status=active, and a high min_impact threshold.
    """
    return _default_recall()(
        trw_dir,
        query="*",
        tags=tags,
        min_impact=min_impact,
        max_results=max_results,
        status="active",
    )


__all__ = [
    "focused_recall_zero_match_advisory",
    "recall_baseline_high_impact",
    "recall_focused",
    "recall_for_nudge_pool",
    "recall_for_review_tags",
    "recall_recent_bypass",
]
