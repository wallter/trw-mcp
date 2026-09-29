"""Shared learnings-collector substrate for cross-package MCP tools (PRD-DIST-2000, cycle 749).

Extracted from c746 ``before_edit_hint.py`` to support the multi-source
pattern across all 5 cross-package consumer tools (c746-c748). The
learnings half is **always tier-independent** — free-tier operators
receive learnings via ``trw_recall`` even when the trw-distill sidecar
feature is ungated.

Each consumer tool calls ``collect_learnings(queries=[...])`` with
tool-appropriate queries:

- ``trw_code`` hint mode, single file: ``[file_path, basename(file_path)]``
- ``trw_code`` hint mode, multiple files: per-file [path, basename] flattened
- the codebase-risk-report engine (``trw-mcp code risk`` CLI as of
  PRD-CORE-300 slice S4): top-N risk paths + basenames

Hub files (ANCHOR-HUB-DOWNRANK): lessons anchored to the edited file lead the
hint, except on a hub, a file with more than ``hint_hub_threshold`` active
anchored lessons. There, lessons the text queries also find keep their text
position and anchor-only lessons follow every text row. Knobs:
``hint_hub_downrank`` (default on; off restores anchored-first exactly) and
``hint_hub_threshold`` (default 67). The first anchored fetch is the flag-off
page; only a full page costs one more recall, of threshold + 1 rows, to count.
A non-hub file shows that first page, as with the flag off.

IP boundary: trw-mcp PUBLIC; trw-distill PROPRIETARY. This module
calls trw-mcp's own ``recall_learnings`` only — no trw_distill import.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterator

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)

DEFAULT_TOP_N: int = 5
MAX_QUERIES: int = 10

#: PRD-CORE-278 FR09: collect twice the requested count before partitioning by
#: origin. Every tool behind this seam is keyed on a path in THIS checkout, and
#: returning early at ``top_n`` meant a file-name token matching five learnings
#: from another repository filled the whole hint (L-XIhp, trw_code hint mode on
#: models/config/_loader.py).
_ATTRIBUTION_OVERFETCH: int = 2

#: Named in every message the hub down-rank emits, so an operator can switch it off.
_HUB_DISABLE = "disable with hint_hub_downrank: false in .trw/config.yaml"

_Recall = Callable[..., list[dict[str, object]]]


class LearningSummary(BaseModel):
    """Compact learning entry shown to consumers (subset of full record).

    Reused across all 5 cross-package tools — single shape contract.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    id: str
    summary: str
    impact: float = 0.0
    tags: list[str] = Field(default_factory=list)


def collect_learnings(
    queries: list[str],
    *,
    top_n: int = DEFAULT_TOP_N,
    anchor_file: str | None = None,
    single_page: bool = False,
) -> list[LearningSummary]:
    """Best-effort trw_recall over a list of queries.

    NEVER raises — empty list on any error path. Tier-independent
    (free-tier operators get value even without paid features).

    Args:
        queries: One or more recall queries. Typically file paths +
            basenames, or aggregate-level domain strings. Capped at
            ``MAX_QUERIES`` to bound retrieval cost.
        top_n: Maximum returned summaries (deduped by learning id).
        anchor_file: A repo-relative file whose anchored lessons are recalled
            first, ahead of the queries (PRD-CORE-332 FR06).
        single_page: HINT-RECALL-BUDGET. Take exactly one page per recall
            instead of growing to a deeper one; the pre-edit hint sets this
            to bound its own recall cost under a deadline.

    Returns:
        Up to ``top_n`` LearningSummary objects, deduped by id, in
        first-seen recall order.
    """
    if not queries and not anchor_file:
        return []
    try:
        from trw_mcp.state.learning_injection import recall_learnings
    except Exception:  # justified: scan-resilience -- optional module import failure is handled gracefully
        logger.warning("learning_injection_import_failed", exc_info=True)
        return []
    if single_page:
        # The deadline-bounded hint also skips the daemon's cross-encoder rerank (~2s/page on a real
        # store vs ~0.2s without): the hint needs fast lessons, not reranked precision.
        recall_learnings = functools.partial(recall_learnings, single_page=True, rerank=False)
    deduplicated: set[str] = set()
    out: list[LearningSummary] = []
    collect_target = top_n * _ATTRIBUTION_OVERFETCH
    rows_by_id: dict[str, dict[str, object]] = {}
    # The anchored recall goes first through the same dedupe loop, so its rows lead and a
    # full anchored page ends collection before any text query, as the early return does.
    # On a hub file its rows are held back until after every text row instead.
    anchored, held = _anchored_rows(recall_learnings, anchor_file, collect_target)
    for rows in _row_batches(recall_learnings, anchored, queries, held, collect_target):
        for r in rows:
            if not isinstance(r, dict):
                continue
            rid = r.get("id")
            if not isinstance(rid, str) or rid in deduplicated:
                continue
            if r.get("status", "active") != "active":  # an obsolete/resolved lesson is never shown
                continue
            deduplicated.add(rid)
            rows_by_id[rid] = r
            summary = r.get("summary")
            if not isinstance(summary, str):
                continue
            impact = r.get("impact", 0.0)
            tags = r.get("tags", [])
            out.append(
                LearningSummary(
                    id=rid,
                    summary=summary,
                    impact=float(impact) if isinstance(impact, (int, float)) else 0.0,
                    tags=[str(t) for t in tags] if isinstance(tags, list) else [],
                )
            )
            if len(out) >= collect_target:
                return _attributable_first(out, rows_by_id, top_n)
    return _attributable_first(out, rows_by_id, top_n)


def _recall_rows(recall: _Recall, query: str, limit: int, anchor_file: str | None) -> list[dict[str, object]]:
    """One recall's rows, or ``[]`` after logging ``recall_learnings_failed`` (the hint is best-effort)."""
    try:
        if anchor_file is None:
            return recall(query, max_results=limit)
        return recall(query, max_results=limit, anchor_file=anchor_file)
    except Exception:  # trw-fail-silent-allow: moved from collect_learnings; the hint is best-effort, the failure is logged as recall_learnings_failed and the other requests still run
        logger.warning("recall_learnings_failed", query=query, exc_info=True)
        return []


def _anchored_rows(
    recall: _Recall, anchor_file: str | None, collect_target: int
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """``(lead, held)``: the anchored rows that lead the hint, or, on a hub file, the rows held until last.

    The first fetch is exactly the flag-off call (*collect_target* rows), and a
    non-hub file always leads with that page unchanged. A hub has more than
    ``hint_hub_threshold`` anchored rows. When the page cannot decide that (it
    is full and the threshold is at least its size), one second fetch of
    ``threshold + 1`` rows only counts them; it never supplies the rows shown.
    """
    if not anchor_file:
        return [], []
    from trw_mcp.models.config import get_config

    page = _recall_rows(recall, anchor_file, collect_target, anchor_file)
    config = get_config()
    if not config.hint_hub_downrank:
        return page, []
    threshold = config.hint_hub_threshold
    count = len(page)
    if count >= collect_target and threshold >= count:
        count = len(_recall_rows(recall, anchor_file, threshold + 1, anchor_file))
    if count <= threshold:
        return page, []
    logger.debug("anchor_hub_downranked", file=anchor_file, held=len(page), threshold=threshold, disable=_HUB_DISABLE)
    return [], page


def _row_batches(
    recall: _Recall,
    anchored: list[dict[str, object]],
    queries: list[str],
    held: list[dict[str, object]],
    collect_target: int,
) -> Iterator[list[dict[str, object]]]:
    """The anchored rows, each text query's rows (recalled lazily, so an early return skips the rest), then *held*."""
    yield anchored
    for q in queries[:MAX_QUERIES]:
        if isinstance(q, str) and q:
            yield _recall_rows(recall, q, collect_target, None)
    yield held


def _attributable_first(
    summaries: list[LearningSummary],
    rows_by_id: dict[str, dict[str, object]],
    top_n: int,
) -> list[LearningSummary]:
    """Order this project's own learnings first, then truncate (FR09)."""
    from trw_mcp.state._origin_project import demote_unattributable

    ordered = demote_unattributable([rows_by_id.get(item.id, {"id": item.id}) for item in summaries])
    by_id = {item.id: item for item in summaries}
    return [by_id[str(row["id"])] for row in ordered if str(row.get("id", "")) in by_id][:top_n]


def build_file_queries(file_path: str) -> list[str]:
    """Build the standard query list for a file-targeted tool.

    Returns ``[file_path, basename(file_path)]``, with the basename added only when
    it differs from the full path. Centralized so the convention is one thing for
    every file-targeted tool.

    This used to accept ``kind: Literal["file_path_basename", "explicit"]`` — a
    typed, defaulted routing option that the body **never read**. Passing
    ``kind="explicit"``, the only reason the parameter existed, silently produced
    the basename fan-out it was asking to avoid. All three call sites used the
    default, so nothing was harmed; it was a knob that did nothing, which this
    project has a standing rule against (PRD-QUAL-131 deleted 45 of them for the
    same reason). Deleted rather than implemented: no caller wants the other
    behaviour, and a dormant option is worse than no option because a reader
    reasonably assumes a declared Literal is honoured.
    """
    import os

    queries = [file_path]
    basename = os.path.basename(file_path)
    if basename and basename != file_path:
        queries.append(basename)
    return queries


__all__ = [
    "DEFAULT_TOP_N",
    "MAX_QUERIES",
    "LearningSummary",
    "build_file_queries",
    "collect_learnings",
]
