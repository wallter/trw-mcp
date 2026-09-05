"""PRD-CORE-247-FR05: report and clear the offline-write reconciliation queue.

A session-start step, in its own module rather than appended to the 492-line
``_ceremony_session_start_steps.py``.

**What it does.** ``trw-mcp local learn`` marks every offline write with the
transient tag :data:`~trw_mcp.state._constants.RECONCILE_PENDING_TAG` (FR04).
This step queries for those rows, reports their count and identifiers on the
``trw_session_start`` result, and then removes the tag from **exactly the rows it
reported**.

**Why reporting precedes clearing.** A row written between the query and the
clear keeps its tag and is reported at the next session start, rather than being
silently dropped (NFR04). The clear iterates the reported identifiers, never a
second query.

**Why a failure here is not an error.** A reconciliation report is diagnostic. It
records a structured degradation and leaves ``success: true``, the precedent
PRD-CORE-227-FR01 set for degraded recall: the mandated first action must never
be taken down by an advisory.

Forward-only and idempotent (NFR05): rows written before this change carry no
tag and are invisible to the query, so there is no backfill and nothing to
reverse. Running twice converges — the second run reports zero.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.models.typed_dicts import (
    ReconciledLocalWritesDict as ReconciledLocalWritesDict,
)
from trw_mcp.state._constants import DEFAULT_LIST_LIMIT, DEFAULT_NAMESPACE, RECONCILE_PENDING_TAG
from trw_mcp.tools._ceremony_degradations import DegradationCollector

logger = structlog.get_logger(__name__)


def _pending_entries(trw_dir: Path) -> list[tuple[str, list[str]]]:
    """Return ``(learning_id, current_tags)`` for every pending offline write.

    The tag predicate is pushed into SQL by ``list_entries(tags=...)``, so this
    is a narrow indexed query and not a full-store scan on every session start.
    """
    from trw_mcp.state.memory_adapter import get_backend

    backend = get_backend(trw_dir)
    entries = backend.list_entries(
        namespace=DEFAULT_NAMESPACE,
        limit=DEFAULT_LIST_LIMIT,
        tags=[RECONCILE_PENDING_TAG],
    )
    return [(str(entry.id), list(entry.tags)) for entry in entries]


def _live_tags(trw_dir: Path, learning_id: str) -> list[str] | None:
    """Re-read this row's tags from the store, or ``None`` if it is gone.

    ``update_learning`` takes the FULL tag list and replaces it, so the set it is
    given must be computed from the row as it is NOW. Computing it from the
    query-time snapshot instead makes the clear a lost-update: a tag another
    process added between the query and the clear is silently erased, and it is
    erased by the step whose whole job is to remove exactly one tag.

    This narrows the window to a single read-modify-write rather than closing it
    — the storage layer exposes no atomic remove-tag. The residual race drops a
    tag written inside that window; the previous shape dropped one written any
    time in the whole step, across every row.
    """
    from trw_mcp.state.memory_adapter import get_backend

    entry = get_backend(trw_dir).get(learning_id, namespace=DEFAULT_NAMESPACE)
    return None if entry is None else list(entry.tags)


def step_reconcile_local_writes(
    trw_dir: Path,
    degradations: DegradationCollector | None = None,
) -> ReconciledLocalWritesDict:
    """Report the pending offline writes, then clear the tag from those rows.

    Always returns a result — a zero count is the honest answer to "what
    bypassed the online path", and an absent field would be indistinguishable
    from a step that never ran.

    Args:
        trw_dir: Resolved ``.trw`` directory.
        degradations: The per-call collector. A failure to clear an individual
            row is recorded there and the row keeps its tag for the next
            session; it is never allowed to drop the report.
    """
    from trw_mcp.state.memory_adapter import update_learning

    pending = _pending_entries(trw_dir)
    learning_ids = [learning_id for learning_id, _tags in pending]
    cleared = 0
    for learning_id, _snapshot_tags in pending:
        try:
            # Re-read immediately before the write, never from the snapshot: the
            # update replaces the whole tag list, so a concurrent tag added since
            # the query would be erased by the step that only means to remove one.
            live_tags = _live_tags(trw_dir, learning_id)
            if live_tags is None:
                # Deleted between query and clear: nothing to clear, and the row
                # was still honestly reported as pending in this session.
                continue
            if RECONCILE_PENDING_TAG not in live_tags:
                # Another process already cleared it. Count it: the tag is gone,
                # which is the state this step exists to reach.
                cleared += 1
                continue
            remaining = [tag for tag in live_tags if tag != RECONCILE_PENDING_TAG]
            result = update_learning(trw_dir, learning_id, tags=remaining)
        except Exception as exc:  # justified: one unclearable row must not drop the report
            if degradations is not None:
                degradations.record("reconcile_local_writes", exc)
            else:
                logger.debug("reconcile_clear_failed", learning_id=learning_id, exc_info=True)
            continue
        if result.get("status") not in {"error", "invalid", "not_found"}:
            cleared += 1
    if learning_ids:
        logger.info(
            "reconciled_local_writes",
            pending=len(learning_ids),
            cleared=cleared,
        )
    return ReconciledLocalWritesDict(
        pending=len(learning_ids),
        learning_ids=learning_ids,
        cleared=cleared,
    )


__all__ = ["ReconciledLocalWritesDict", "step_reconcile_local_writes"]
