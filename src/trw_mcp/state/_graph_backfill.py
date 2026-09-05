"""Forced knowledge-graph backfill over the existing memory corpus.

Belongs to the :mod:`trw_mcp.state.memory_adapter` facade. Re-exported there
for back-compat.

Why this exists (F5 root-cause B): ``update_entry_graph`` is only called
per-single-entry on the MCP store path. Nothing ever loops over the EXISTING
corpus to build edges, so a project that accumulated thousands of learnings
before graph-wiring landed keeps an empty ``memory_graph_edges`` table forever.
That is a one-time historical debt: the store path has enriched every write
synchronously since PRD-FIX-COMPOUNDING-2 FR01, and PRD-CORE-245's schema-5
migration populates ``memory_tags`` for the whole corpus in one pass, so the
tag half of the graph needs no sweep at all. What is left for this pass is the
MATERIALISED edge types — similarity, consolidation, co-anchored and
cross-project validation — for entries written before that wiring existed.

CRITICAL path discipline (L-zRB4 / L-kydf): the backfill MUST run on the MCP
singleton's OWN ``backend._conn`` via :func:`update_entry_graph`. It must NOT
use ``schedule_graph_update`` / ``create_backend_from_config`` — those resolve a
DIFFERENT ``<namespace>/memory.db`` file than the singleton serves, so edges
would land in a file nobody reads (the exact historical bug). Edges built here
are visible to the singleton because we share its connection.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

import structlog
from trw_memory.graph import update_entry_graph
from trw_memory.models.config import MemoryConfig
from trw_memory.storage.interface import EntryCursor

from trw_mcp.state._constants import DEFAULT_LIST_LIMIT, DEFAULT_NAMESPACE
from trw_mcp.state._memory_connection import get_backend

logger = structlog.get_logger(__name__)

_NAMESPACE = DEFAULT_NAMESPACE

#: Where one project records how far its sweep has read. A sibling of the store
#: it describes, so deleting ``.trw/memory/`` resets both together.
_SWEEP_STATE_FILENAME = "graph-backfill.json"

#: Schema tag on the sweep-state file. An unrecognised version is treated as no
#: state at all — re-sweeping is wasted work, never wrong work.
_SWEEP_STATE_VERSION = 1


class _SweepState:
    """One namespace's resume point: how far the sweep read, and whether it ended.

    The sweep used to infer this from the graph itself — an entry that appeared
    as an edge ``source_id`` had been processed. That inference died with
    PRD-CORE-245 FR07: tag co-occurrence was 95.96% of the edges, and it is
    derived now rather than stored, so an entry with no similarity neighbour and
    no consolidation lineage never becomes a source no matter how many times it
    is enriched. Every deliver re-read and re-enriched the entire corpus, and
    the sweep could never report itself finished. Progress has to be recorded
    where the sweep can read it back.
    """

    __slots__ = ("complete", "cursor")

    def __init__(self, cursor: EntryCursor | None, complete: bool) -> None:
        self.cursor = cursor
        self.complete = complete


def _state_path(trw_dir: Path) -> Path:
    return trw_dir / "memory" / _SWEEP_STATE_FILENAME


def _load_state(trw_dir: Path) -> _SweepState:
    """Read this namespace's resume point. Any unreadable state means "start over"."""
    try:
        raw = json.loads(_state_path(trw_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _SweepState(None, False)
    if not isinstance(raw, dict) or raw.get("version") != _SWEEP_STATE_VERSION:
        return _SweepState(None, False)
    namespaces = raw.get("namespaces")
    entry = namespaces.get(_NAMESPACE) if isinstance(namespaces, dict) else None
    if not isinstance(entry, dict):
        return _SweepState(None, False)
    updated_at = entry.get("updated_at")
    entry_id = entry.get("entry_id")
    cursor = (
        EntryCursor(updated_at=str(updated_at), entry_id=str(entry_id))
        if isinstance(updated_at, str) and isinstance(entry_id, str)
        else None
    )
    return _SweepState(cursor, bool(entry.get("complete")))


def _save_state(trw_dir: Path, state: _SweepState) -> None:
    """Persist the resume point atomically, preserving other namespaces' entries."""
    path = _state_path(trw_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    namespaces = raw.get("namespaces") if isinstance(raw, dict) else None
    payload: dict[str, Any] = {
        "version": _SWEEP_STATE_VERSION,
        "namespaces": dict(namespaces) if isinstance(namespaces, dict) else {},
    }
    payload["namespaces"][_NAMESPACE] = {
        "updated_at": state.cursor.updated_at if state.cursor else None,
        "entry_id": state.cursor.entry_id if state.cursor else None,
        "complete": state.complete,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        # Fail-open: losing the resume point costs a repeated sweep, not
        # correctness, and must never break the deliver that triggered it.
        logger.debug("graph_backfill_state_write_failed", exc_info=True)


def backfill_graph(
    trw_dir: Path,
    *,
    deadline_seconds: float | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    """Build graph edges for existing un-graphed entries on the singleton conn.

    Reads the project namespace in ``(updated_at, id)`` order from wherever the
    last call stopped, calling :func:`update_entry_graph` on each entry. Reuses a
    stored embedding vector when one is present so no re-embed happens (mirrors
    the FR02 single-embed discipline of the store path). Once the listing is
    exhausted the sweep records itself complete and every later call is a no-op:
    the store path graphs each new entry as it is written, so there is nothing
    behind the sweep for it to come back for.

    Args:
        trw_dir: project ``.trw`` directory whose singleton backend is enriched.
        deadline_seconds: optional wall-clock budget (opportunistic callers pass
            e.g. ``2.0``); processing stops once the budget is exhausted and the
            NEXT call resumes at the entry after the last one processed, rather
            than re-reading the corpus from the top.
        limit: optional max number of entries to read in this call.

    Returns:
        ``{"processed": N, "edges_built": N, "skipped": N, "failed": N}``.

    Fail-open: per-entry enrichment failures are counted and logged, never
    raised — graph backfill is best-effort and must not break its callers. The
    cursor advances past a failed entry so one poison row cannot stall the sweep
    forever; the store path remains the primary writer for anything it strands.
    """
    backend = get_backend(trw_dir)
    conn = getattr(backend, "_conn", None)
    if not isinstance(conn, sqlite3.Connection):
        logger.debug("graph_backfill_skipped", reason="no_sqlite_connection")
        return {"processed": 0, "edges_built": 0, "skipped": 0, "failed": 0}

    state = _load_state(trw_dir)
    if state.complete:
        logger.debug("graph_backfill_skipped", reason="sweep_complete")
        return {"processed": 0, "edges_built": 0, "skipped": 0, "failed": 0}

    page_limit = limit if limit is not None else DEFAULT_LIST_LIMIT
    entries = backend.list_entries(namespace=_NAMESPACE, limit=page_limit, after=state.cursor)
    sec_cfg = MemoryConfig(storage_path=str(trw_dir / "memory"))
    start = time.monotonic()

    processed = 0
    edges_built = 0
    skipped = 0
    failed = 0
    interrupted = False

    for entry in entries:
        if deadline_seconds is not None and (time.monotonic() - start) >= deadline_seconds:
            interrupted = True
            break
        if entry.metadata.get("system_canary") == "true":
            skipped += 1
        else:
            try:
                result = update_entry_graph(
                    entry, backend, embedding=_stored_embedding(backend, entry.id), config=sec_cfg
                )
                edges_built += sum(value for value in result.values() if isinstance(value, int))
                processed += 1
            except (sqlite3.Error, ValueError, RuntimeError):
                failed += 1
                logger.debug("graph_backfill_entry_failed", entry_id=entry.id, exc_info=True)
        state.cursor = EntryCursor.from_entry(entry)

    # The listing is exhausted only when it returned a short page AND nothing
    # cut this pass short: a deadline break leaves entries below the cursor.
    state.complete = not interrupted and len(entries) < page_limit
    _save_state(trw_dir, state)

    logger.info(
        "graph_backfill_complete",
        processed=processed,
        edges_built=edges_built,
        skipped=skipped,
        failed=failed,
        deadline_seconds=deadline_seconds,
        sweep_complete=state.complete,
    )
    return {
        "processed": processed,
        "edges_built": edges_built,
        "skipped": skipped,
        "failed": failed,
    }


def _stored_embedding(backend: Any, entry_id: str) -> list[float] | None:
    """Return the entry's stored vector, so the sweep never re-embeds."""
    stored = backend.get_stored_embeddings([entry_id])
    embedding = stored.get(entry_id)
    return list(embedding) if embedding is not None else None
