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


def _default_recall() -> Callable[..., list[dict[str, object]]]:
    """Lazy import to avoid a cycle on package init."""
    from trw_mcp.state.memory_adapter import recall_learnings

    return recall_learnings


def parse_entry_impact(entry: dict[str, object]) -> float:
    """Coerce an entry's ``impact`` field to ``float``, defensively.

    Used as the sort key by :func:`recall_for_review_tags` to impact-rank the
    tag union (L-hzMb). A malformed/missing ``impact`` (``None``, ``""``, a
    non-numeric string, or an absent key) falls back to ``0.0`` rather than
    raising and dropping the whole recall.
    """
    try:
        return float(str(entry.get("impact", 0.0) or 0.0))
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------------------
# Session-start factories
# ---------------------------------------------------------------------------


def recall_session_start(
    trw_dir: Path,
    query: str,
    *,
    max_results: int,
    min_impact: float = 0.3,
) -> list[dict[str, object]]:
    """The one session_start recall (PRD-CORE-294 FR02), focused or ``"*"``.

    There is no second, query-independent baseline recall to merge with it.
    Full rows are acquired so ``verbose=True`` can return them without a
    second lookup.
    """
    return _default_recall()(
        trw_dir,
        query=query,
        min_impact=min_impact,
        max_results=max_results,
        compact=False,
        status="active",  # session start must not surface obsolete/archived learnings
    )


#: Emitted only when a focused session_start recall returns zero rows, so it costs
#: nothing on the normal path (token-budget rule: advisory fields are omitted when
#: they carry no signal).
FOCUSED_ZERO_MATCH_ADVISORY = (
    "Focused recall matched 0 entries. Broaden the query or call trw_recall(query=..., options={'min_impact': 0})."
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
    """The *max_results* highest-impact active learnings carrying ANY of *tags*.

    Used by ``state/claude_md`` review/publish flow. The store ANDs its tag
    filter and lists newest first, so each tag is recalled in full
    (``max_results=0``: every row past the SQL tag/impact/status filter, up to
    ``DEFAULT_LIST_LIMIT``), unioned by id, and ranked by impact here (L-hzMb).

    Serial per-tag fan-out (one SQL call per tag in ``_REVIEW_TAGS``, 6 today)
    rather than a single query, because the store ANDs a multi-value ``tags``
    filter. Measured on this machine (2026-09-23, ``.venv/bin/python``, a fresh
    temp SQLite store seeded via ``backend.store()`` directly, 6 tags cycled
    round-robin across the rows): 5,000 rows -> ~187 ms; 20,000 rows -> ~729 ms
    for the full fan-out + union + sort. Both are well under any interactive or
    ``trw_deliver``-path budget; REVIEW.md generation is not on a request's hot
    path. Revisit if a store's review-tagged population reaches the ~100k range.
    """
    recall = _default_recall()
    by_id: dict[object, dict[str, object]] = {}
    for tag in tags:
        for entry in recall(trw_dir, query="*", tags=[tag], min_impact=min_impact, max_results=0, status="active"):
            by_id.setdefault(entry.get("id"), entry)
    ranked = sorted(by_id.values(), key=parse_entry_impact, reverse=True)
    # max_results=0 means "unlimited" throughout recall_learnings (see the
    # max_results=0 fan-out call above); match that convention here instead of
    # slicing to an empty list.
    return ranked[: max_results or None]


__all__ = [
    "FOCUSED_ZERO_MATCH_ADVISORY",
    "parse_entry_impact",
    "recall_for_nudge_pool",
    "recall_for_review_tags",
    "recall_session_start",
]
