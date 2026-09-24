"""Forced knowledge-graph backfill over the existing memory corpus: the resume point.

Belongs to the :mod:`trw_mcp.state.memory_adapter` facade. Re-exported there
for back-compat.

Why this exists (F5 root-cause B): the store graphs each row it writes, but
nothing loops over a corpus written before that wiring (or merged in from a
checkout), so its MATERIALISED edges -- similarity, consolidation, co-anchored
and cross-project validation -- are never built. The store builds them one page
at a time (``MemoryStore.graph_backfill``, the daemon's ``memory_graph_backfill``),
on the connection that serves the rows. This module keeps where the sweep
stopped, beside the checkout, so a deadline-bounded deliver resumes it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import structlog

from trw_mcp.state._constants import DEFAULT_LIST_LIMIT

logger = structlog.get_logger(__name__)

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

    def __init__(self, cursor: dict[str, str] | None, complete: bool) -> None:
        self.cursor = cursor
        self.complete = complete


def _state_path(trw_dir: Path) -> Path:
    return trw_dir / "memory" / _SWEEP_STATE_FILENAME


def _load_state(trw_dir: Path, namespace: str) -> _SweepState:
    """Read *namespace*'s resume point. Any unreadable state means "start over"."""
    try:
        raw = json.loads(_state_path(trw_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _SweepState(None, False)
    if not isinstance(raw, dict) or raw.get("version") != _SWEEP_STATE_VERSION:
        return _SweepState(None, False)
    namespaces = raw.get("namespaces")
    entry = namespaces.get(namespace) if isinstance(namespaces, dict) else None
    if not isinstance(entry, dict):
        return _SweepState(None, False)
    updated_at = entry.get("updated_at")
    entry_id = entry.get("entry_id")
    cursor = (
        {"updated_at": updated_at, "entry_id": entry_id}
        if isinstance(updated_at, str) and isinstance(entry_id, str)
        else None
    )
    return _SweepState(cursor, bool(entry.get("complete")))


def _save_state(trw_dir: Path, namespace: str, state: _SweepState | None) -> None:
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
    fresh = {"updated_at": None, "entry_id": None, "complete": False}
    if state is None:  # every recorded namespace starts over
        payload["namespaces"] = dict.fromkeys(payload["namespaces"], fresh)
    else:
        payload["namespaces"][namespace] = {**fresh, **(state.cursor or {}), "complete": state.complete}
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
    """Graph the next page of this checkout's existing rows, resuming where the last call stopped.

    Once the store reports the listing exhausted, the sweep records itself
    complete and every later call is a no-op: the store graphs each new row as
    it is written, so nothing is left behind it.

    Args:
        trw_dir: project ``.trw`` directory whose store is swept.
        deadline_seconds: optional wall-clock budget (deliver passes e.g. ``2.0``);
            the store stops once it is spent and the next call resumes there.
        limit: optional max number of rows to read in this call.

    Returns:
        ``{"processed": N, "edges_built": N, "skipped": N, "failed": N}``. A row whose
        enrichment fails is counted and passed, so one poison row cannot stall the sweep.
    """
    from trw_mcp.state._store_selection import selected_store

    store, namespace = selected_store(trw_dir)
    state = _load_state(trw_dir, namespace)
    if state.complete:
        logger.debug("graph_backfill_skipped", reason="sweep_complete")
        return {"processed": 0, "edges_built": 0, "skipped": 0, "failed": 0}

    page = store.graph_backfill(
        namespace, state.cursor, limit if limit is not None else DEFAULT_LIST_LIMIT, deadline_seconds
    )
    _save_state(trw_dir, namespace, _SweepState(page["next"], bool(page["complete"])))
    logger.info(
        "graph_backfill_complete",
        namespace=namespace,
        sweep_complete=page["complete"],
        deadline_seconds=deadline_seconds,
    )
    return {key: int(page[key]) for key in ("processed", "edges_built", "skipped", "failed")}
