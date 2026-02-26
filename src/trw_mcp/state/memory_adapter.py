"""Adapter layer between trw-mcp learning tools and trw-memory SQLite backend.

Provides singleton backend access, one-time YAML→SQLite migration, and
CRUD operations that preserve the exact return shapes of the original
YAML-based learning tools.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from trw_memory.migration.from_trw import migrate_entries_dir
from trw_memory.models.memory import MemoryEntry, MemoryStatus
from trw_memory.storage.sqlite_backend import SQLiteBackend

from trw_mcp.models.config import get_config

logger = structlog.get_logger()

# Module-level singleton
_backend: SQLiteBackend | None = None
_backend_lock = threading.Lock()

_SENTINEL_NAME = ".migrated"
_NAMESPACE = "default"
_MAX_ENTRIES = 10_000  # Effective "unlimited" cap for list/search operations


# ---------------------------------------------------------------------------
# Backend lifecycle
# ---------------------------------------------------------------------------

def get_backend(trw_dir: Path | None = None) -> SQLiteBackend:
    """Return the singleton SQLiteBackend, creating it on first call.

    The database lives at ``trw_dir / memory / memory.db``.
    Auto-calls :func:`ensure_migrated` on first access.

    Args:
        trw_dir: Path to the ``.trw`` directory.  Auto-resolved when *None*.

    Returns:
        Shared :class:`SQLiteBackend` instance.
    """
    global _backend  # noqa: PLW0603
    if _backend is not None:
        return _backend

    with _backend_lock:
        if _backend is not None:
            return _backend  # pragma: no cover — race guard

        if trw_dir is None:
            from trw_mcp.state._paths import resolve_trw_dir
            trw_dir = resolve_trw_dir()

        memory_dir = trw_dir / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        db_path = memory_dir / "memory.db"

        cfg = get_config()
        backend = SQLiteBackend(db_path, dim=cfg.retrieval_embedding_dim)
        ensure_migrated(trw_dir, backend)
        _backend = backend
        return _backend


def reset_backend() -> None:
    """Close and discard the singleton backend (for tests)."""
    global _backend  # noqa: PLW0603
    with _backend_lock:
        if _backend is not None:
            _backend.close()
            _backend = None


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def ensure_migrated(trw_dir: Path, backend: SQLiteBackend) -> dict[str, int]:
    """One-time migration of YAML learning entries into SQLite.

    Idempotent: writes a sentinel file on success; subsequent calls are no-ops.
    Individual entry failures are logged and skipped — never aborts the batch.

    Args:
        trw_dir: Path to the ``.trw`` directory.
        backend: Active :class:`SQLiteBackend` to store entries in.

    Returns:
        Dict with ``migrated`` and ``skipped`` counts.
    """
    sentinel = trw_dir / "memory" / _SENTINEL_NAME
    if sentinel.exists():
        return {"migrated": 0, "skipped": 0}

    cfg = get_config()
    entries_dir = trw_dir / cfg.learnings_dir / cfg.entries_dir
    if not entries_dir.exists():
        # Fresh project — nothing to migrate
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("migrated_at=" + datetime.now(timezone.utc).isoformat())
        return {"migrated": 0, "skipped": 0}

    migrated = 0
    skipped = 0

    try:
        memory_entries = migrate_entries_dir(entries_dir)
    except Exception:
        logger.warning("memory_migration_read_failed", entries_dir=str(entries_dir))
        return {"migrated": 0, "skipped": 0}

    for entry in memory_entries:
        try:
            # Ensure namespace is set
            if not entry.namespace or entry.namespace == "":
                entry = entry.model_copy(update={"namespace": _NAMESPACE})
            backend.store(entry)
            migrated += 1
        except Exception:
            skipped += 1
            logger.debug("memory_migration_entry_skipped", entry_id=entry.id)

    # Only write sentinel on success
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(
        f"migrated_at={datetime.now(timezone.utc).isoformat()}\n"
        f"migrated={migrated}\nskipped={skipped}\n"
    )

    logger.info(
        "memory_migration_complete",
        migrated=migrated,
        skipped=skipped,
    )
    return {"migrated": migrated, "skipped": skipped}


# ---------------------------------------------------------------------------
# Field mapping helpers
# ---------------------------------------------------------------------------

def _memory_to_learning_dict(entry: MemoryEntry, *, compact: bool = False) -> dict[str, object]:
    """Convert a :class:`MemoryEntry` to the dict shape returned by trw_recall.

    The returned dict matches the YAML-era learning entry format so callers
    (FRAMEWORK.md, hooks, etc.) see no API change.

    Args:
        entry: Memory entry from SQLite.
        compact: When True, return only essential fields.

    Returns:
        Dict with ``id``, ``summary``, ``tags``, ``impact``, ``status``, etc.
    """
    base: dict[str, object] = {
        "id": entry.id,
        "summary": entry.content,
        "tags": entry.tags,
        "impact": entry.importance,
        "status": entry.status.value if isinstance(entry.status, MemoryStatus) else str(entry.status),
    }
    if compact:
        return base

    base.update({
        "detail": entry.detail,
        "evidence": entry.evidence,
        "source_type": entry.source,
        "source_identity": entry.source_identity,
        "created": entry.created_at.date().isoformat() if entry.created_at else "",
        "updated": entry.updated_at.date().isoformat() if entry.updated_at else "",
        "access_count": entry.access_count,
        "last_accessed_at": (
            entry.last_accessed_at.date().isoformat() if entry.last_accessed_at else None
        ),
        "q_value": entry.q_value,
        "q_observations": entry.q_observations,
        "recurrence": entry.recurrence,
        "shard_id": entry.metadata.get("shard_id", None),
    })
    return base


def _learning_to_memory_entry(
    learning_id: str,
    summary: str,
    detail: str,
    *,
    tags: list[str] | None = None,
    evidence: list[str] | None = None,
    impact: float = 0.5,
    shard_id: str | None = None,
    source_type: str = "agent",
    source_identity: str = "",
) -> MemoryEntry:
    """Build a :class:`MemoryEntry` from trw_learn parameters."""
    metadata: dict[str, str] = {}
    if shard_id:
        metadata["shard_id"] = shard_id

    return MemoryEntry(
        id=learning_id,
        content=summary,
        detail=detail,
        tags=tags or [],
        evidence=evidence or [],
        importance=impact,
        source=source_type,
        source_identity=source_identity,
        namespace=_NAMESPACE,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# CRUD operations (return shapes match original YAML tools)
# ---------------------------------------------------------------------------

def store_learning(
    trw_dir: Path,
    learning_id: str,
    summary: str,
    detail: str,
    *,
    tags: list[str] | None = None,
    evidence: list[str] | None = None,
    impact: float = 0.5,
    shard_id: str | None = None,
    source_type: str = "agent",
    source_identity: str = "",
) -> dict[str, object]:
    """Store a learning entry in SQLite and return the tool result dict.

    Return shape matches ``trw_learn`` output:
    ``{"learning_id", "path", "status", "distribution_warning"}``.
    """
    backend = get_backend(trw_dir)
    entry = _learning_to_memory_entry(
        learning_id, summary, detail,
        tags=tags, evidence=evidence, impact=impact,
        shard_id=shard_id, source_type=source_type,
        source_identity=source_identity,
    )
    backend.store(entry)

    logger.info("memory_store_learning", learning_id=learning_id)
    return {
        "learning_id": learning_id,
        "path": f"sqlite://{learning_id}",
        "status": "recorded",
        "distribution_warning": "",
    }


def recall_learnings(
    trw_dir: Path,
    query: str,
    *,
    tags: list[str] | None = None,
    min_impact: float = 0.0,
    status: str | None = None,
    max_results: int = 25,
    compact: bool = False,
) -> list[dict[str, object]]:
    """Search learnings from SQLite and return dicts matching recall shape.

    For wildcard queries (``*`` or empty), lists all entries.
    Otherwise performs keyword search.
    """
    backend = get_backend(trw_dir)
    is_wildcard = query.strip() in ("*", "")

    mem_status: MemoryStatus | None = None
    if status is not None:
        try:
            mem_status = MemoryStatus(status)
        except ValueError:
            pass

    if is_wildcard:
        entries = backend.list_entries(
            status=mem_status,
            namespace=_NAMESPACE,
            limit=max_results if max_results > 0 else _MAX_ENTRIES,
        )
    else:
        # For multi-word queries, tokenize and intersect per-token results so
        # that each token can match in *any* field (content OR detail OR tags).
        # This allows "database postgresql" to match an entry where "database"
        # is in the summary and "postgresql" is in the detail.
        tokens = query.split()
        top_k = max_results if max_results > 0 else _MAX_ENTRIES
        if len(tokens) <= 1:
            entries = backend.search(
                query,
                top_k=top_k,
                tags=tags,
                status=mem_status,
                min_importance=min_impact,
                namespace=_NAMESPACE,
            )
        else:
            # Intersect: entry must match ALL tokens
            token_id_sets: list[set[str]] = []
            token_entry_map: dict[str, Any] = {}
            first_token_ordered: list[Any] = []
            for i, token in enumerate(tokens):
                token_results = backend.search(
                    token,
                    top_k=top_k,
                    tags=tags,
                    status=mem_status,
                    min_importance=min_impact,
                    namespace=_NAMESPACE,
                )
                token_ids: set[str] = set()
                for e in token_results:
                    token_ids.add(e.id)
                    token_entry_map[e.id] = e
                token_id_sets.append(token_ids)
                if i == 0:
                    first_token_ordered = list(token_results)
            # Only keep entries that matched every token, preserving
            # relevance ordering from the first token's search results
            common_ids = token_id_sets[0]
            for s in token_id_sets[1:]:
                common_ids = common_ids & s
            entries = [e for e in first_token_ordered if e.id in common_ids]

    results: list[dict[str, object]] = []
    for entry in entries:
        d = _memory_to_learning_dict(entry, compact=compact)
        entry_impact = float(str(d.get("impact", 0.0)))
        if entry_impact < min_impact:
            continue
        # Tag filter for list_entries (search already filters)
        if tags and is_wildcard:
            entry_tags = d.get("tags", [])
            if isinstance(entry_tags, list):
                if not any(t in entry_tags for t in tags):
                    continue
        results.append(d)

    return results


def update_learning(
    trw_dir: Path,
    learning_id: str,
    *,
    status: str | None = None,
    detail: str | None = None,
    impact: float | None = None,
    summary: str | None = None,
) -> dict[str, str]:
    """Update a learning entry in SQLite.

    Return shape matches ``trw_learn_update`` output:
    ``{"learning_id", "changes", "status"}``.
    """
    backend = get_backend(trw_dir)
    existing = backend.get(learning_id)
    if existing is None:
        return {"error": f"Learning {learning_id} not found", "status": "not_found"}

    fields: dict[str, Any] = {}
    changes: list[str] = []

    if status is not None:
        valid_statuses = {"active", "resolved", "obsolete"}
        if status not in valid_statuses:
            return {
                "error": f"Invalid status '{status}'. Must be one of: {valid_statuses}",
                "status": "invalid",
            }
        fields["status"] = status
        changes.append(f"status→{status}")

    if detail is not None:
        fields["detail"] = detail
        changes.append("detail updated")

    if summary is not None:
        fields["content"] = summary
        changes.append("summary updated")

    if impact is not None:
        if not 0.0 <= impact <= 1.0:
            return {"error": f"Impact must be 0.0-1.0, got {impact}", "status": "invalid"}
        fields["importance"] = impact
        changes.append(f"impact→{impact}")

    if not changes:
        return {"learning_id": learning_id, "status": "no_changes"}

    backend.update(learning_id, **fields)

    logger.info("memory_update_learning", learning_id=learning_id, changes=changes)
    return {
        "learning_id": learning_id,
        "changes": ", ".join(changes),
        "status": "updated",
    }


def find_entry_by_id(trw_dir: Path, learning_id: str) -> dict[str, object] | None:
    """Look up a single learning entry by ID.

    Returns the dict in learning format, or None if not found.
    """
    backend = get_backend(trw_dir)
    entry = backend.get(learning_id)
    if entry is None:
        return None
    return _memory_to_learning_dict(entry)


def list_active_learnings(
    trw_dir: Path,
    *,
    min_impact: float = 0.0,
    limit: int = 10000,
) -> list[dict[str, object]]:
    """List all active learning entries from SQLite.

    Used by claude_md.py for CLAUDE.md promotion and analytics.
    """
    backend = get_backend(trw_dir)
    entries = backend.list_entries(
        status=MemoryStatus.ACTIVE,
        namespace=_NAMESPACE,
        limit=limit,
    )
    results: list[dict[str, object]] = []
    for entry in entries:
        if entry.importance >= min_impact:
            results.append(_memory_to_learning_dict(entry))
    return results


def count_entries(trw_dir: Path) -> int:
    """Return total number of entries in the SQLite store."""
    backend = get_backend(trw_dir)
    return backend.count(namespace=_NAMESPACE)


def update_access_tracking(trw_dir: Path, learning_ids: list[str]) -> None:
    """Increment access_count and last_accessed_at for recalled entries."""
    backend = get_backend(trw_dir)
    now = datetime.now(timezone.utc)
    for lid in learning_ids:
        try:
            entry = backend.get(lid)
            if entry is not None:
                backend.update(
                    lid,
                    access_count=entry.access_count + 1,
                    last_accessed_at=now,
                )
        except Exception:
            continue
