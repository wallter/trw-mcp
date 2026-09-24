"""Which rows one store shows for a recall: the rule search and ``ids=`` share.

Belongs to the ``memory_adapter.py`` facade. Every ``MemoryStore`` reads its
stores (a checkout's project file then its federated stores, or a daemon's
namespaces) in one order and hands the candidate rows here, so dedup,
admission, the representative row, per-store caps and the bounded deeper fetch
have one implementation (PRD-CORE-294 FR01; PRD-CORE-280 FR01). An id is shown
by the first store, in read order, holding a row admission passes; search shows
that row only where it is itself a hit, so a stub hydrates to the row it showed.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING

from trw_mcp.state._constants import DEFAULT_LIST_LIMIT

if TYPE_CHECKING:
    from trw_memory.models.memory import MemoryEntry, MemoryStatus

    from trw_mcp.state._store_selection import RecallSpec

__all__ = ["REPRESENTATIVE_LOOKUP_SLACK", "apply_entry_filters", "recall_namespaces", "settle_ids", "take_hits"]

# Lookups a store may spend past its cap on ids that end up not shown (refused,
# filtered out, claimed by an earlier store, or not themselves a hit), so a store
# full of refused hits costs a bounded number of reads (PRD-CORE-294 FR01).
REPRESENTATIVE_LOOKUP_SLACK = 64

RowsFor = Callable[[str], Iterable["MemoryEntry"]]


def apply_entry_filters(
    entry: MemoryEntry, tags: list[str] | None, mem_status: MemoryStatus | None, min_impact: float
) -> bool:
    """Whether *entry* passes the recall's impact, status and tag filters."""
    if min_impact > 0.0 and entry.importance < min_impact:
        return False
    if mem_status is not None and entry.status != mem_status:
        return False
    return not (tags and not set(tags).issubset(set(entry.tags)))


def take_hits(
    page: Callable[[int], list[MemoryEntry]],
    spec: RecallSpec,
    *,
    cap: int,
    seen: set[str],
    rows_for: RowsFor | None = None,
    claimed: Callable[[str], bool] | None = None,
) -> list[MemoryEntry]:
    """Up to *cap* admitted rows from one store's candidates, fetching deeper past refused ones.

    *page(k)* returns the store's first ``k`` ranked (or listed) candidates.
    *rows_for* reads every row the store holds under an id; ``None`` means the
    store has one namespace, so the hit is the id's only row. An id in *seen*,
    or *claimed* by an earlier store, is not shown. Taken ids join *seen*.
    """
    admission = spec.admission
    is_wildcard = spec.query.strip() in ("*", "")
    budget = cap + REPRESENTATIVE_LOOKUP_SLACK
    resolved: dict[str, MemoryEntry | None] = {}
    top_k = spec.top_k
    while True:
        hits = page(top_k)
        by_id: dict[str, list[MemoryEntry]] = {}
        for hit in hits:
            by_id.setdefault(hit.id, []).append(hit)
        matched = {(hit.id, hit.namespace) for hit in hits}
        taken: list[MemoryEntry] = []
        for entry_id, rows in by_id.items():
            if len(taken) >= cap:
                break
            if entry_id in seen:
                continue
            if entry_id not in resolved:
                if len(resolved) >= budget:
                    break
                owned_elsewhere = claimed is not None and claimed(entry_id)
                candidates = rows_for(entry_id) if rows_for is not None else rows
                resolved[entry_id] = None if owned_elsewhere else admission.representative(candidates)
            row = resolved[entry_id]
            # A representative the search would exclude (a filter, or not itself a
            # hit) drops the id rather than showing a twin that hydrates elsewhere.
            if (
                row is not None
                and apply_entry_filters(row, spec.tags, admission.mem_status, spec.min_impact)
                and (is_wildcard or (row.id, row.namespace) in matched)
            ):
                taken.append(row)
        if len(taken) >= cap or len(resolved) >= budget or len(hits) < top_k or top_k >= DEFAULT_LIST_LIMIT:
            break
        top_k = min(top_k * 4, DEFAULT_LIST_LIMIT)
    seen.update(entry.id for entry in taken)
    return taken


def settle_ids(spec: RecallSpec, found: dict[str, MemoryEntry], rows_for: RowsFor, *, cap: int | None = None) -> None:
    """Settle the requested ids this store holds an admitted row for; at most *cap* of them, in request order."""
    taken = 0
    for entry_id in dict.fromkeys(spec.ids):
        if cap is not None and taken >= cap:
            return
        if entry_id in found:
            continue
        row = spec.admission.representative(rows_for(entry_id))
        if row is not None:
            found[entry_id] = row
            taken += 1


def user_recall_cap() -> int:
    """Per-recall cap on ``user:local`` hits (``recall_user_tier_cap``, default 5)."""
    from trw_mcp.models.config import get_config

    return max(0, get_config().recall_user_tier_cap)


def recall_namespaces(
    spec: RecallSpec,
    namespaces: Sequence[str],
    *,
    page: Callable[[str, int], list[MemoryEntry]],
    row: Callable[[str, str], MemoryEntry | None],
) -> list[MemoryEntry]:
    """``MemoryStore.recall`` for a store whose namespaces are separate stores, read in order.

    The project namespace comes first and shows up to ``top_k`` rows; ``user:local``
    follows, capped as the local user store is. *page(ns, k)* returns a
    namespace's first ``k`` candidates (status-aware, unadmitted); *row(ns, id)*
    reads one row.
    """
    from trw_mcp.state._tier_routing import USER_NAMESPACE

    caps = [user_recall_cap() if namespace == USER_NAMESPACE else spec.top_k for namespace in namespaces]

    def rows_in(namespace: str) -> RowsFor:
        return lambda entry_id: filter(None, [row(namespace, entry_id)])

    if spec.ids:
        found: dict[str, MemoryEntry] = {}
        for namespace, cap in zip(namespaces, caps, strict=True):
            settle_ids(spec, found, rows_in(namespace), cap=cap if namespace == USER_NAMESPACE else None)
        return [found[entry_id] for entry_id in dict.fromkeys(spec.ids) if entry_id in found]

    admission = spec.admission
    seen: set[str] = set()
    admitted: list[MemoryEntry] = []
    for index, (namespace, cap) in enumerate(zip(namespaces, caps, strict=True)):
        earlier = namespaces[:index]

        def claimed(entry_id: str, earlier: Sequence[str] = earlier) -> bool:
            return any(admission.representative(rows_in(prior)(entry_id)) is not None for prior in earlier)

        admitted.extend(
            take_hits(
                functools.partial(page, namespace), spec, cap=cap, seen=seen, claimed=claimed if earlier else None
            )
        )
    return admitted
