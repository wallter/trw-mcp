"""Opt-in, non-destructive user-tier backfill -- PRD-CORE-185 FR08.

Reclassifies EXISTING high-portability *project-tier* learnings into the
machine-local *user* tier so a box that accumulated portable knowledge before
the user tier existed can promote it after the fact.

Design (FR08 / NFR02 / NFR05):

* **Default OFF.** Nothing here runs during a normal session; it is invoked
  only by an explicit maintenance call (``reclassify_to_user_tier``).
* **Non-destructive.** Qualifying entries are *copied* into the user store. The
  project copy is left intact unless the caller passes ``move=True`` (explicit
  confirmation).
* **Idempotent.** Entries already present in the user store are skipped, so
  re-running produces no duplicates.
* **Reuses the FR05 classifier** (``classify_tier``) plus a conservative impact
  floor -- it does NOT fork the portability heuristic.
* **Observability.** Emits structlog records with ``outcome``/``action`` fields
  (never the reserved ``event=`` kwarg).

This is a focused sibling of ``memory_adapter`` (NFR07): the maintenance path
lives here so the adapter facade stays under the 350 eff-LOC gate. It is
re-exported through ``memory_adapter`` for the FR08 grep/wiring assertion.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from trw_memory.models.memory import MemoryEntry
    from trw_memory.storage.sqlite_backend import SQLiteBackend

# Conservative default impact floor: only promote learnings worth carrying
# box-wide. Mirrors the "under-promote rather than over-promote" stance (D2).
_DEFAULT_MIN_IMPACT = 0.5


def _is_portable(entry: MemoryEntry) -> bool:
    """Return True if a project-tier entry classifies as portable (-> user)."""
    from trw_mcp.state._tier_routing import classify_tier

    return (
        classify_tier(
            source_type=str(entry.source),
            tags=list(entry.tags),
            domain=list(getattr(entry, "domain", []) or []),
            summary=entry.content,
            detail=entry.detail,
        )
        == "user"
    )


def reclassify_to_user_tier(
    trw_dir: Path,
    *,
    dry_run: bool = True,
    move: bool = False,
    min_impact: float = _DEFAULT_MIN_IMPACT,
    limit: int = 10_000,
) -> dict[str, list[str]]:
    """Promote high-portability project learnings into the user tier (FR08).

    Args:
        trw_dir: The project ``.trw`` directory whose store is scanned.
        dry_run: When True (default), only REPORT candidates; write nothing.
        move: When True, delete the project copy after a successful user-store
            write (explicit, non-default confirmation). Default copies only.
        min_impact: Conservative impact floor; entries below it are not promoted.
        limit: Max project entries to scan.

    Returns:
        A report dict ``{"candidates", "promoted", "skipped"}`` of learning IDs.
        ``candidates`` = portable + above the impact floor; ``promoted`` = newly
        written to the user store (empty in dry-run); ``skipped`` = already in
        the user store (idempotency).
    """
    from trw_mcp.state._tier_routing import USER_NAMESPACE, user_scope_present
    from trw_mcp.state._user_tier import get_user_backend
    from trw_mcp.state.memory_adapter import _NAMESPACE, get_backend

    report: dict[str, list[str]] = {"candidates": [], "promoted": [], "skipped": []}

    if not user_scope_present():
        # No user-scope store -> nothing to promote into. Fail-safe no-op.
        logger.info("user_tier_backfill_skipped", outcome="no_user_scope", action="reclassify")
        return report

    project_backend = get_backend(trw_dir)
    project_entries = project_backend.list_entries(namespace=_NAMESPACE, limit=limit)

    user_backend = get_user_backend()
    # core185-5: the dedup set must cover the ENTIRE user store, independent of
    # the project-scan ``limit``. The user store is box-wide and may hold far
    # more entries than a single project's scan window; truncating this fetch to
    # ``limit`` lets an already-promoted entry beyond the window slip the
    # ``in existing_user_ids`` check and be RE-PROMOTED (duplicate). ``limit=0``
    # is NOT an "unlimited" sentinel in the backend (it means ``LIMIT 0`` -> zero
    # rows), so use ``sys.maxsize`` to fetch all ids.
    existing_user_ids = {e.id for e in user_backend.list_entries(namespace=USER_NAMESPACE, limit=sys.maxsize)}

    for entry in project_entries:
        if entry.importance < min_impact:
            continue
        if not _is_portable(entry):
            continue
        report["candidates"].append(entry.id)
        if entry.id in existing_user_ids:
            report["skipped"].append(entry.id)
            continue
        if dry_run:
            continue
        _promote_entry(entry, user_backend, USER_NAMESPACE)
        report["promoted"].append(entry.id)
        if move:
            project_backend.delete(entry.id)

    logger.info(
        "user_tier_backfill_done",
        outcome="ok",
        action="reclassify",
        dry_run=dry_run,
        move=move,
        candidates=len(report["candidates"]),
        promoted=len(report["promoted"]),
        skipped=len(report["skipped"]),
    )
    return report


def _promote_entry(entry: MemoryEntry, user_backend: SQLiteBackend, user_namespace: str) -> None:
    """Copy a project entry into the user store under ``user_namespace``.

    Non-destructive on the source: a fresh :class:`MemoryEntry` is built with
    the user namespace stamped so the copy de-dupes/queries against the user
    store. The original project entry is untouched here (the optional ``move``
    delete happens in the caller after a successful write).
    """
    promoted = entry.model_copy(deep=True)
    promoted.namespace = user_namespace
    if isinstance(promoted.metadata, dict):
        promoted.metadata = {**promoted.metadata, "tier": "user", "promoted_from": "project"}
    user_backend.store(promoted)
