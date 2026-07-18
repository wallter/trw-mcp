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

    Used by the session_start focused path -- BM25 + vector hybrid search
    against the caller's task description. Compact mode by default.
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

    Used by ``_try_learning_nudge_content`` and ``select_learning_injection_content``
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
    "recall_baseline_high_impact",
    "recall_focused",
    "recall_for_nudge_pool",
    "recall_for_review_tags",
    "recall_recent_bypass",
]
