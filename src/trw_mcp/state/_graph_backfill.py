"""Forced knowledge-graph backfill over the existing memory corpus.

Belongs to the :mod:`trw_mcp.state.memory_adapter` facade. Re-exported there
for back-compat.

Why this exists (F5 root-cause B): ``update_entry_graph`` is only called
per-single-entry on the MCP store path. Nothing ever loops over the EXISTING
corpus to build edges, so a project that accumulated thousands of learnings
before graph-wiring landed (or while sustained writer pressure deferred every
opportunistic pass) keeps an empty ``memory_graph_edges`` table forever.

CRITICAL path discipline (L-zRB4 / L-kydf): the backfill MUST run on the MCP
singleton's OWN ``backend._conn`` via :func:`update_entry_graph`. It must NOT
use ``schedule_graph_update`` / ``create_backend_from_config`` — those resolve a
DIFFERENT ``<namespace>/memory.db`` file than the singleton serves, so edges
would land in a file nobody reads (the exact historical bug). Edges built here
are visible to the singleton because we share its connection.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import structlog
from trw_memory.graph import update_entry_graph
from trw_memory.models.config import MemoryConfig

from trw_mcp.state._constants import DEFAULT_LIST_LIMIT, DEFAULT_NAMESPACE
from trw_mcp.state._memory_connection import get_backend

logger = structlog.get_logger(__name__)

_NAMESPACE = DEFAULT_NAMESPACE


def _entries_with_edges(conn: sqlite3.Connection) -> set[str]:
    """Return the set of entry IDs that already appear as an edge ``source_id``.

    ``update_entry_graph`` builds edges originating from the enriched entry, so
    an entry present as a ``source_id`` has already been graphed and can be
    skipped on a re-run (idempotency). Fail-open: any query error returns an
    empty set so the backfill simply re-processes everything rather than
    crashing.
    """
    try:
        rows = conn.execute("SELECT DISTINCT source_id FROM memory_graph_edges").fetchall()
    except sqlite3.Error:
        logger.debug("graph_backfill_source_scan_failed", exc_info=True)
        return set()
    return {str(row[0]) for row in rows if row and row[0] is not None}


def backfill_graph(
    trw_dir: Path,
    *,
    deadline_seconds: float | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    """Build graph edges for existing un-graphed entries on the singleton conn.

    Iterates active entries in the project namespace, calling
    :func:`update_entry_graph` for each entry that does not yet appear as an
    edge source. Reuses a stored embedding vector when one is present so no
    re-embed happens (mirrors the FR02 single-embed discipline of the store
    path).

    Args:
        trw_dir: project ``.trw`` directory whose singleton backend is enriched.
        deadline_seconds: optional wall-clock budget (opportunistic callers pass
            e.g. ``2.0``); processing stops once the budget is exhausted, leaving
            the remainder for a later pass. ``None`` means run to completion.
        limit: optional max number of entries to enrich in this call.

    Returns:
        ``{"processed": N, "edges_built": N, "skipped": N, "failed": N}``.

    Fail-open: per-entry enrichment failures are counted and logged, never
    raised — graph backfill is best-effort and must not break its callers.
    """
    backend = get_backend(trw_dir)
    conn = getattr(backend, "_conn", None)
    if not isinstance(conn, sqlite3.Connection):
        logger.debug("graph_backfill_skipped", reason="no_sqlite_connection")
        return {"processed": 0, "edges_built": 0, "skipped": 0, "failed": 0}

    already_sourced = _entries_with_edges(conn)
    entries = backend.list_entries(
        namespace=_NAMESPACE,
        limit=limit if limit is not None else DEFAULT_LIST_LIMIT,
    )
    sec_cfg = MemoryConfig(storage_path=str(trw_dir / "memory"))
    start = time.monotonic()

    processed = 0
    edges_built = 0
    skipped = 0
    failed = 0

    for entry in entries:
        if deadline_seconds is not None and (time.monotonic() - start) >= deadline_seconds:
            break
        if entry.metadata.get("system_canary") == "true":
            skipped += 1
            continue
        if entry.id in already_sourced:
            skipped += 1
            continue
        stored = backend.get_stored_embeddings([entry.id])
        embedding = stored.get(entry.id)
        try:
            result = update_entry_graph(entry, backend, embedding=embedding, config=sec_cfg)
            edges_built += sum(v for v in result.values() if isinstance(v, int))
            processed += 1
        except (sqlite3.Error, ValueError, RuntimeError):
            failed += 1
            logger.debug("graph_backfill_entry_failed", entry_id=entry.id, exc_info=True)

    logger.info(
        "graph_backfill_complete",
        processed=processed,
        edges_built=edges_built,
        skipped=skipped,
        failed=failed,
        deadline_seconds=deadline_seconds,
    )
    return {
        "processed": processed,
        "edges_built": edges_built,
        "skipped": skipped,
        "failed": failed,
    }
