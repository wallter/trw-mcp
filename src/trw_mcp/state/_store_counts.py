"""The one bounded reader of "how many entries does this project's store hold?".

PRD-FIX-141-FR04. Three surfaces print a learning count and none of them said
what it counted. On 2026-09-16, against a store holding 1,356 rows, the
generated ``.trw/INSTRUCTIONS.md`` said ``0 learnings from 0 prior sessions``,
the ``trw://learnings/summary`` resource said ``Total learnings: 9`` and
``trw_recall`` said ``total_available=25`` (learning L-Rikf). Each number was
individually explainable — locally authored deliveries, an analytics counter
written after the render, a bounded pre-cap match population — and jointly
false, because a reader has no way to know which question any of them answered.

This module answers exactly one of those questions, once, so every surface that
wants "the store's own inventory" gets the same number over the same population.

The number is the store's own: ``memory_status``'s ``health`` block for the
checkout's pinned namespace, measured by the daemon over its store, with canaries
excluded (PRD-CORE-280). ``None`` means it was not measured -- an unpinned
checkout, an unreachable daemon -- and rendering that as ``0`` is the exact
defect FR04 removes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog

from trw_mcp.state._store_selection import NamespaceHealth, StoreUnavailableError

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class StoreCounts:
    """Entry counts for one namespace of one project store.

    ``total`` is ``local + synced`` by construction — the split is what lets a
    caller name its population instead of printing an unqualified number.
    """

    total: int
    local: int
    synced: int


def store_health(trw_dir: Path) -> NamespaceHealth:
    """This checkout's namespace health, measured by its store. Raises when the store cannot be reached.

    Measuring only: an unpinned checkout is refused on its config alone, since a
    health probe or a count opens no checkout's memory.db.
    """
    from trw_mcp.state import _store_selection

    with _store_selection.measuring_only():
        store, namespace = _store_selection.selected_store(trw_dir)
    return store.health(namespace)


def read_store_counts(trw_dir: Path) -> StoreCounts | None:
    """Return the checkout's entry counts, or ``None`` when the store was not read.

    ``None`` is deliberately distinct from ``StoreCounts(0, 0, 0)``, which is the
    answer for a store that was read and is genuinely empty.
    """
    try:
        health = store_health(trw_dir)
    except (StoreUnavailableError, ValueError) as exc:
        logger.warning("store_counts_unreadable", reason=type(exc).__name__)
        return None
    return StoreCounts(total=health["entries"], local=health["entries"] - health["synced"], synced=health["synced"])


def store_entry_count(trw_dir: Path) -> int | None:
    """Return the namespace's entry count, or ``None`` when it was not measured.

    The thin accessor for callers that need the inventory size and not its
    provenance split (``trw_recall``'s ``store_count``).
    """
    counts = read_store_counts(trw_dir)
    return None if counts is None else counts.total


__all__ = ["StoreCounts", "read_store_counts", "store_entry_count", "store_health"]
