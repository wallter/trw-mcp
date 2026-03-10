"""Adapter layer between trw-mcp learning tools and trw-memory SQLite backend.

Provides singleton backend access, one-time YAML→SQLite migration, and
CRUD operations that preserve the exact return shapes of the original
YAML-based learning tools.

When ``embeddings_enabled=True`` in config, the adapter:
- Generates embeddings on store via :class:`LocalEmbeddingProvider`
- Uses hybrid search (BM25 + dense + RRF fusion) on recall
- Backfills embeddings for existing entries on first activation
"""

from __future__ import annotations

import contextlib
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from trw_memory.migration.from_trw import migrate_entries_dir
from trw_memory.models.memory import MemoryEntry, MemoryStatus
from trw_memory.storage.sqlite_backend import SQLiteBackend

from trw_mcp.models.config import get_config
from trw_mcp.state._constants import DEFAULT_LIST_LIMIT, DEFAULT_NAMESPACE

logger = structlog.get_logger()

# Module-level singletons
_backend: SQLiteBackend | None = None
_backend_lock = threading.Lock()

_embedder: Any = None  # LocalEmbeddingProvider | None
_embedder_lock = threading.Lock()
_embedder_checked: bool = False

_SENTINEL_NAME = ".migrated"
_NAMESPACE = DEFAULT_NAMESPACE
_MAX_ENTRIES = DEFAULT_LIST_LIMIT


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
    global _backend
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
    global _backend
    with _backend_lock:
        if _backend is not None:
            _backend.close()
            _backend = None
    reset_embedder()


# ---------------------------------------------------------------------------
# Embedder lifecycle
# ---------------------------------------------------------------------------

def get_embedder() -> Any:
    """Return the singleton LocalEmbeddingProvider, or None if unavailable.

    Only attempts initialization when ``embeddings_enabled=True`` in config.
    The result is cached — repeated calls are cheap.
    """
    global _embedder, _embedder_checked
    if _embedder_checked:
        return _embedder

    with _embedder_lock:
        if _embedder_checked:
            return _embedder  # pragma: no cover — race guard

        cfg = get_config()
        if not cfg.embeddings_enabled:
            _embedder_checked = True
            return None

        try:
            from trw_memory.embeddings.local import LocalEmbeddingProvider
            provider = LocalEmbeddingProvider(
                model_name=cfg.retrieval_embedding_model,
                dim=cfg.retrieval_embedding_dim,
            )
            if provider.available():
                _embedder = provider
                logger.info(
                    "embedder_initialized",
                    model=cfg.retrieval_embedding_model,
                    dim=cfg.retrieval_embedding_dim,
                )
            else:
                logger.info(
                    "embeddings_enabled_but_deps_missing",
                    hint="pip install trw-memory[embeddings]",
                )
        except Exception:
            # FR06: Log at warning so embedder failures are visible in logs.
            # Do NOT set _embedder_checked — allows retry on next call or
            # after reset_embedder() (e.g. session restart).
            logger.warning("embedder_init_failed", exc_info=True)
            return _embedder

        _embedder_checked = True
        return _embedder


def reset_embedder() -> None:
    """Reset the embedder singleton (for tests)."""
    global _embedder, _embedder_checked
    with _embedder_lock:
        _embedder = None
        _embedder_checked = False


def check_embeddings_status() -> dict[str, object]:
    """Check embedding readiness and return status for session_start advisory.

    Returns a dict with:
    - ``enabled``: whether config has embeddings_enabled=True
    - ``available``: whether deps are installed and model loads
    - ``advisory``: human-readable message (empty when everything is fine)
    """
    cfg = get_config()
    if not cfg.embeddings_enabled:
        return {"enabled": False, "available": False, "advisory": ""}

    embedder = get_embedder()
    if embedder is not None:
        return {"enabled": True, "available": True, "advisory": ""}

    return {
        "enabled": True,
        "available": False,
        "advisory": (
            "Embeddings enabled but sentence-transformers not installed. "
            "Run: pip install trw-memory[embeddings]"
        ),
    }


def _embed_and_store(backend: SQLiteBackend, entry_id: str, text: str) -> None:
    """Generate embedding for text and upsert into vector table. Fail-silent."""
    embedder = get_embedder()
    if embedder is None:
        return
    try:
        vector = embedder.embed(text)
        if vector is not None:
            backend.upsert_vector(entry_id, vector)
    except (OSError, ValueError, RuntimeError):
        # justified: embedding is optional enrichment — store succeeds without it.
        logger.debug("embed_and_store_failed", entry_id=entry_id)


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
        logger.warning(
            "memory_migration_read_failed",
            exc_info=True,
            entries_dir=str(entries_dir),
        )
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
            logger.warning(
                "memory_migration_entry_skipped",
                exc_info=True,
                entry_id=entry.id,
            )

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

    QUAL-018 FR03: Infers topic tags from the summary before storing.

    Return shape matches ``trw_learn`` output:
    ``{"learning_id", "path", "status", "distribution_warning"}``.
    """
    # QUAL-018 FR03/FR05: Infer topic tags and append (no duplicates)
    from trw_mcp.state.analytics import infer_topic_tags

    enriched_tags = list(tags) if tags else []
    inferred = infer_topic_tags(summary, enriched_tags)
    if inferred:
        enriched_tags.extend(inferred)

    backend = get_backend(trw_dir)
    entry = _learning_to_memory_entry(
        learning_id, summary, detail,
        tags=enriched_tags, evidence=evidence, impact=impact,
        shard_id=shard_id, source_type=source_type,
        source_identity=source_identity,
    )
    backend.store(entry)

    # Generate and store embedding when enabled
    embed_text = f"{summary} {detail}"
    _embed_and_store(backend, learning_id, embed_text)

    logger.info("memory_store_learning", learning_id=learning_id)
    return {
        "learning_id": learning_id,
        "path": f"sqlite://{learning_id}",
        "status": "recorded",
        "distribution_warning": "",
    }


def _search_entries(
    backend: SQLiteBackend,
    query: str,
    *,
    top_k: int = 25,
    tags: list[str] | None = None,
    mem_status: MemoryStatus | None = None,
    min_impact: float = 0.0,
) -> list[MemoryEntry]:
    """Search entries using hybrid (keyword + vector RRF) or keyword fallback.

    When embedder is available, runs keyword search and sqlite-vec vector search
    in parallel, then fuses via Reciprocal Rank Fusion. Otherwise falls back to
    multi-token intersection keyword search.
    """
    # Always run keyword search
    keyword_results = _keyword_search(
        backend, query, top_k=top_k, tags=tags,
        mem_status=mem_status, min_impact=min_impact,
    )

    # Try vector search when embedder is available
    embedder = get_embedder()
    if embedder is None:
        return keyword_results

    try:
        query_vec = embedder.embed(query)
        if query_vec is None:
            return keyword_results

        cfg = get_config()
        vector_hits = backend.search_vectors(query_vec, top_k=cfg.hybrid_vector_candidates)
        if not vector_hits:
            return keyword_results

        # RRF fusion: merge keyword and vector rankings
        keyword_ranking = [(e.id, 1.0 / (i + 1)) for i, e in enumerate(keyword_results)]
        vector_ranking = [(eid, score) for eid, score in vector_hits]

        from trw_memory.retrieval.fusion import rrf_fuse
        fused = rrf_fuse([keyword_ranking, vector_ranking], k=cfg.hybrid_rrf_k)

        # Build id→entry map from keyword results + vector-matched entries
        entry_map: dict[str, MemoryEntry] = {e.id: e for e in keyword_results}
        # Fetch any vector-only hits not already in keyword results
        for eid, _ in vector_hits:
            if eid not in entry_map:
                entry = backend.get(eid)
                if entry is not None:
                    # Apply same filters as keyword search
                    if min_impact > 0.0 and entry.importance < min_impact:
                        continue
                    if mem_status is not None and entry.status != mem_status:
                        continue
                    if tags and not set(tags).issubset(set(entry.tags)):
                        continue
                    entry_map[eid] = entry

        results: list[MemoryEntry] = []
        for eid, _ in fused[:top_k]:
            if eid in entry_map:
                results.append(entry_map[eid])

        logger.debug(
            "hybrid_recall_complete",
            keyword_hits=len(keyword_results),
            vector_hits=len(vector_hits),
            fused=len(results),
        )
        return results

    except (OSError, ValueError, RuntimeError):
        logger.debug("vector_search_failed_fallback_to_keyword", query=query[:80])
        return keyword_results


def _keyword_search(
    backend: SQLiteBackend,
    query: str,
    *,
    top_k: int = 25,
    tags: list[str] | None = None,
    mem_status: MemoryStatus | None = None,
    min_impact: float = 0.0,
) -> list[MemoryEntry]:
    """Multi-token intersection keyword search."""
    tokens = query.split()
    if len(tokens) <= 1:
        return backend.search(
            query,
            top_k=top_k,
            tags=tags,
            status=mem_status,
            min_importance=min_impact,
            namespace=_NAMESPACE,
        )

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

    if not token_id_sets:
        return []

    common_ids = token_id_sets[0]
    for s in token_id_sets[1:]:
        common_ids = common_ids & s
    return [e for e in first_token_ordered if e.id in common_ids]


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
        with contextlib.suppress(ValueError):
            mem_status = MemoryStatus(status)

    if is_wildcard:
        entries = backend.list_entries(
            status=mem_status,
            namespace=_NAMESPACE,
            limit=max_results if max_results > 0 else _MAX_ENTRIES,
        )
    else:
        top_k = max_results if max_results > 0 else _MAX_ENTRIES
        entries = _search_entries(
            backend, query, top_k=top_k, tags=tags,
            mem_status=mem_status, min_impact=min_impact,
        )

    results: list[dict[str, object]] = []
    for entry in entries:
        d = _memory_to_learning_dict(entry, compact=compact)
        entry_impact = float(str(d.get("impact", 0.0)))
        if entry_impact < min_impact:
            continue
        # Tag filter for list_entries (search already filters)
        if tags and is_wildcard:
            entry_tags = d.get("tags", [])
            if isinstance(entry_tags, list) and not any(t in entry_tags for t in tags):
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
    limit: int = DEFAULT_LIST_LIMIT,
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


def list_entries_by_status(
    trw_dir: Path,
    *,
    status: str = "active",
    min_impact: float = 0.0,
    limit: int = DEFAULT_LIST_LIMIT,
) -> list[dict[str, object]]:
    """Return all entries with the given status as learning dicts.

    PRD-FIX-033-FR01: Single SQLite query for bulk entry retrieval.
    """
    try:
        mem_status = MemoryStatus(status)
    except ValueError:
        return []
    backend = get_backend(trw_dir)
    entries = backend.list_entries(
        status=mem_status,
        namespace=_NAMESPACE,
        limit=limit,
    )
    results: list[dict[str, object]] = []
    for entry in entries:
        if entry.importance >= min_impact:
            results.append(_memory_to_learning_dict(entry))
    return results


def find_yaml_path_for_entry(trw_dir: Path, entry_id: str) -> Path | None:
    """Resolve the YAML file path for a given entry_id.

    PRD-FIX-033-FR05: YAML path resolution for cold archive calls.
    """
    import re as _re

    cfg = get_config()
    entries_dir = trw_dir / cfg.learnings_dir / cfg.entries_dir
    if not entries_dir.exists():
        return None

    sanitized = _re.sub(r"[^a-zA-Z0-9_\-]", "-", entry_id)

    # Try exact match first
    candidate = entries_dir / f"{sanitized}.yaml"
    if candidate.exists():
        return candidate

    # Fall back to partial match
    for yaml_file in entries_dir.glob("*.yaml"):
        if yaml_file.name == "index.yaml":
            continue
        if sanitized in yaml_file.stem or entry_id in yaml_file.stem:
            return yaml_file

    return None


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
            # justified: access tracking is best-effort maintenance — failing
            # to increment a counter must not break recall results.
            logger.warning(
                "access_tracking_update_failed",
                exc_info=True,
                entry_id=lid,
            )
            continue


def backfill_embeddings(trw_dir: Path) -> dict[str, int]:
    """Generate embeddings for all entries that don't have one yet.

    Called on first activation of embeddings (session_start with
    embeddings_enabled=True and deps available). Idempotent — skips
    entries that already have a vector stored.

    Returns counts: ``{"embedded": N, "skipped": N, "failed": N}``.
    """
    embedder = get_embedder()
    if embedder is None:
        return {"embedded": 0, "skipped": 0, "failed": 0}

    backend = get_backend(trw_dir)
    entries = backend.list_entries(namespace=_NAMESPACE, limit=_MAX_ENTRIES)

    embedded = 0
    skipped = 0
    failed = 0

    for entry in entries:
        # Check if vector already exists by attempting a search
        # with high top_k — cheaper than adding a get_vector method
        try:
            text = f"{entry.content} {entry.detail}"
            if not text.strip():
                skipped += 1
                continue

            vector = embedder.embed(text)
            if vector is None:
                failed += 1
                continue

            backend.upsert_vector(entry.id, vector)
            embedded += 1
        except (OSError, ValueError, RuntimeError):
            failed += 1

    logger.info(
        "embeddings_backfill_complete",
        embedded=embedded,
        skipped=skipped,
        failed=failed,
    )
    return {"embedded": embedded, "skipped": skipped, "failed": failed}
