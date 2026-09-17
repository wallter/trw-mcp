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

Design constraints, all of them from what the existing readers got wrong:

- **Bounded SQL, never materialisation.** ``state._memory_lookups.count_entries``
  deserialises up to 100,000 entries to count them; that is unusable on the
  instruction-render and recall hot paths, which is part of why neither of them
  had a store count at all.
- **No backend initialisation.** A short-lived read-only connection over the
  store file, like the pipeline-health probes, so this never contends with the
  singleton's writer lock or triggers a cold open.
- **Namespace-scoped.** A file-wide count is not this project's inventory.
- **Not-measured is a value, not a zero.** ``None`` means the store could not be
  read. Rendering that as ``0`` is the exact defect FR04 removes.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import structlog

from trw_mcp.state._constants import DEFAULT_NAMESPACE

logger = structlog.get_logger(__name__)

#: Rows whose ``source`` is this were pulled from another project by team sync.
#: They are real entries and they are recalled, but they were not authored here,
#: and a surface that prints one total for both populations without saying so is
#: the count-provenance defect this module exists to end.
SYNCED_SOURCE: str = "team_sync"

#: Health-check rows the framework writes into its own store. Excluded for the
#: same reason ``count_entries`` excludes them: they are instrumentation, not
#: knowledge, and counting them inflates every inventory claim.
_CANARY_MARKER: str = '%"system_canary"%'


@dataclass(frozen=True, slots=True)
class StoreCounts:
    """Entry counts for one namespace of one project store.

    ``total`` is ``local + synced`` by construction — the split is what lets a
    caller name its population instead of printing an unqualified number.
    """

    total: int
    local: int
    synced: int


def read_store_counts(trw_dir: Path, *, namespace: str = DEFAULT_NAMESPACE) -> StoreCounts | None:
    """Return *namespace*'s entry counts, or ``None`` when the store was not read.

    ``None`` covers every "we did not look" case — no store file yet, an
    unreadable or locked database, a schema without the columns this counts over.
    It is deliberately distinct from ``StoreCounts(0, 0, 0)``, which is the
    answer for a store that was read and is genuinely empty.
    """
    db_path = trw_dir / "memory" / "memory.db"
    if not db_path.is_file():
        return None
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0) as conn:
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(CASE WHEN source = ? THEN 1 ELSE 0 END), 0) "
                "FROM memories WHERE namespace = ? AND metadata NOT LIKE ?",
                (SYNCED_SOURCE, namespace, _CANARY_MARKER),
            ).fetchone()
    except sqlite3.Error as exc:
        # Fail-open to NOT MEASURED, never to zero: every caller renders the two
        # differently, which is the whole point of the None.
        logger.warning("store_counts_unreadable", reason=type(exc).__name__, path=str(db_path))
        return None
    if row is None:
        return None
    total = int(row[0])
    synced = int(row[1])
    return StoreCounts(total=total, local=total - synced, synced=synced)


def store_entry_count(trw_dir: Path, *, namespace: str = DEFAULT_NAMESPACE) -> int | None:
    """Return the namespace's entry count, or ``None`` when it was not measured.

    The thin accessor for callers that need the inventory size and not its
    provenance split (``trw_recall``'s ``store_count``).
    """
    counts = read_store_counts(trw_dir, namespace=namespace)
    return None if counts is None else counts.total


__all__ = ["SYNCED_SOURCE", "StoreCounts", "read_store_counts", "store_entry_count"]
