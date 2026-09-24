"""Recall path for the memory adapter.

``recall_learnings`` is the public recall entry point (re-exported by the
``memory_adapter`` facade). The checkout's store serves the rows
(``MemoryStore.recall``, PRD-CORE-280 FR01); this module turns them into
learning dicts and ranks a wildcard listing by utility.
"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

import structlog

from trw_mcp.models.typed_dicts import LearningEntryDict
from trw_mcp.state import _store_selection
from trw_mcp.state._constants import DEFAULT_LIST_LIMIT
from trw_mcp.state._memory_transforms import _memory_to_learning_dict
from trw_mcp.state._recall_admission import RecallAdmission
from trw_mcp.state._recall_gate import passive_learnings_allowed
from trw_mcp.state._recall_take import apply_entry_filters
from trw_mcp.state._store_selection import RecallSpec, StoreUnavailableError

if TYPE_CHECKING:
    from pathlib import Path


logger = structlog.get_logger(__name__)

# A store that cannot be opened must not read as "no learnings". ``recall_learnings``
# keeps its list contract for its many callers, and records the failure here for the
# two MCP entry points to surface (``trw_recall`` field, ``trw_session_start`` error).
_STORE_ERROR: ContextVar[str | None] = ContextVar("trw_recall_store_error", default=None)


def pop_store_error() -> str | None:
    """The store failure the last recall in this context hit, cleared on read."""
    message = _STORE_ERROR.get()
    _STORE_ERROR.set(None)
    return message


def _parse_as_of(as_of: str | None) -> datetime | None:
    """PRD-CORE-194 FR03: parse an ISO-8601 ``as_of`` to a tz-aware UTC datetime.

    Returns ``None`` for ``None`` (the default, open-only behavior). Accepts a
    trailing ``Z`` (UTC). A naive parse is assumed UTC so the comparison against
    tz-aware entry windows never raises. Raises ``ValueError`` on a malformed
    string so the boundary can surface a clean validation error rather than crash.
    """
    if as_of is None:
        return None
    try:
        parsed = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"as_of must be an ISO-8601 datetime, got {as_of!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def recall_learnings(
    trw_dir: Path,
    query: str,
    *,
    tags: list[str] | None = None,
    min_impact: float = 0.0,
    status: str | None = None,
    max_results: int = 25,
    compact: bool = False,
    include_tiers: list[str] | None = None,
    as_of: str | None = None,
    include_superseded: bool = False,
) -> list[dict[str, object]]:
    """Search learnings, federating project ∪ user tiers (PRD-CORE-185 FR06/FR07).

    For wildcard queries (``*`` or empty), lists all entries. Otherwise performs
    keyword/hybrid search. When a user-scope store is present, user-tier hits are
    merged in (capped, de-duped); otherwise behavior is project-only and
    byte-identical to the pre-federation path.

    ``include_tiers`` (FR07) scopes ONLY the user-tier federation; project
    entries are ALWAYS included (the project tier is the local source of truth
    and is never excluded). ``None`` (default) and any list containing ``"user"``
    federate the user tier when present; ``["project"]`` (no ``"user"``) yields
    project-only. A user-only query is intentionally not expressible -- passing
    ``["user"]`` still returns project entries plus the federated user tier.

    ``as_of`` / ``include_superseded`` (PRD-CORE-194 FR03) thread the bi-temporal
    validity prior. ``as_of`` is an ISO-8601 string ("what was believed true as of
    T"); a malformed value raises ``ValueError`` (the boundary surfaces it as a
    clean validation error). ``include_superseded=True`` appends superseded records
    AFTER every open one rather than dropping them. The defaults (``as_of=None``,
    ``include_superseded=False``) are byte-identical to the pre-194 path.

    Every learning that reaches the agent passes through here, so the master
    recall switch is enforced here: off, nothing is returned, whatever name the
    caller imported this function under.
    """
    if not passive_learnings_allowed():
        return []
    as_of_dt = _parse_as_of(as_of)
    # One admission policy for search, listing and the by-id fetch (PRD-CORE-294 FR01).
    admission = RecallAdmission.build(trw_dir, status=status, as_of=as_of_dt, include_superseded=include_superseded)
    selection, mem_status = admission.selection, admission.mem_status
    is_wildcard = query.strip() in ("*", "")
    spec = RecallSpec(
        admission=admission,
        query=query,
        tags=tags,
        min_impact=min_impact,
        top_k=max_results if max_results > 0 else DEFAULT_LIST_LIMIT,
        include_user=include_tiers is None or "user" in include_tiers,
    )
    # PRD-CORE-280 FR01: the checkout's store serves the rows; everything below is shared.
    try:
        store, _ = _store_selection.selected_store(trw_dir)
        admitted = store.recall(spec)
    except (
        StoreUnavailableError
    ) as exc:  # trw-fail-silent-allow: recorded in _STORE_ERROR, which trw_recall and trw_session_start surface
        logger.warning("memory_recall_store_unavailable", query=query[:80], error=str(exc))
        _STORE_ERROR.set(str(exc))
        return []
    filtered_entries = admission.order(admitted)
    results: list[LearningEntryDict] = []
    for entry in filtered_entries:
        if is_wildcard and not apply_entry_filters(entry, tags, mem_status, min_impact):
            continue
        if not is_wildcard and entry.importance < min_impact:
            continue
        projected = _memory_to_learning_dict(entry, compact=compact)
        if include_superseded:
            from trw_mcp.state.temporal_order import TEMPORAL_ELIGIBILITY_FIELD

            cast("dict[str, object]", projected)[TEMPORAL_ELIGIBILITY_FIELD] = selection.eligible(entry)
        results.append(projected)

    # R-RANK-002/004: wildcard list_entries orders by updated_at DESC only; route
    # through rank_targeted_by_utility so impact/utility drives order (recency is a decay
    # term, not the sole key). The non-wildcard branch is left to execute_recall.
    ranked_results: list[dict[str, object]] = cast("list[dict[str, object]]", results)
    if is_wildcard and ranked_results:
        ranked_results = _rank_wildcard_by_utility(ranked_results)
        eligible_ids = {entry.id for entry in filtered_entries if selection.eligible(entry)}
        ranked_results.sort(key=lambda row: str(row.get("id", "")) not in eligible_ids)

    logger.info(
        "memory_search_ok",
        query=query[:50],
        result_count=len(ranked_results),
        is_wildcard=is_wildcard,
    )
    return ranked_results


def _rank_wildcard_by_utility(results: list[dict[str, object]]) -> list[dict[str, object]]:
    """Re-rank wildcard recall results so impact/utility drives order.

    R-RANK-002/004: ``backend.list_entries`` returns ``updated_at DESC`` only.
    For a wildcard query every entry has relevance 1.0, so ``rank_targeted_by_utility``
    blends ``(1 - lambda) * 1.0 + lambda * utility`` and the utility term
    (impact + Ebbinghaus recency decay) becomes the sole differentiator. Fails
    open: any ranking error returns the recency-ordered list unchanged.
    """
    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.scoring import rank_targeted_by_utility

        lambda_weight = get_config().recall_utility_lambda
        return rank_targeted_by_utility(results, [], lambda_weight)
    except Exception:  # justified: fail-open, ranking must never block recall
        logger.debug("wildcard_utility_rank_failed", exc_info=True)
        return results
