"""External read-store registry + recall federation -- PRD-CORE-202.

trw-distill can distill a multi-repo estate into ONE consolidated, distributable
trw-memory DB. Before this module there was no MCP consumption path for that
artifact: ``_memory_connection.get_backend()`` returns exactly one project-local
backend and ``trw_recall`` reads only that file (the reproduced dead-end in
feedback ``sub_nCt6qwkie2Hm5D1P``).

This is a focused sibling of ``_user_tier.py`` (PRD-CORE-185 FR02), generalized
from "one machine-local user store" to "N operator-named external corpora":

* ``resolve_external_store_paths`` -- the effective external path set = union of
  ``config.extra_read_stores`` (FR01) and any ``--memory-db`` CLI paths (FR03),
  resolved + de-duped by ``Path.resolve()`` and excluding the project's own
  default store (RISK-02).
* ``get_external_backends`` -- one additive READ-ONLY ``SQLiteBackend`` per valid
  path (per-file singleton, mirroring ``get_user_backend``). A missing /
  non-regular / schema-incompatible path is SKIPPED with a structured
  ``external_store_skipped`` event (FR04); construction never raises into recall.
* ``federate_external_stores`` -- the recall union step (capped by
  ``external_store_recall_cap`` NFR05, de-duped, fail-open NFR03), mirroring
  ``_memory_recall._federate_user_tier``.
* ``assert_writable_backend`` / ``is_external_backend`` -- the write-target guard
  (FR04 / NFR02): no write/prune/consolidate path may target an external corpus.

Read-only enforcement (OQ-04): the trw-memory ``SQLiteBackend`` constructor has
no read-only open mode, so enforcement is application-level -- this module ONLY
ever calls read methods on external backends, and ``assert_writable_backend``
hard-rejects any external backend handed to a write path (defense in depth).

NFR01 (hot-path no-op): with no external paths configured the federation step is
skipped before any backend construction or filesystem stat beyond reading config.
NFR07: all external-store logic lives here so ``_memory_connection.py`` (already
over the 350 eff-LOC gate) and ``_memory_recall.py`` stay within the gate.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from datetime import datetime

    from trw_memory.models.memory import MemoryEntry, MemoryStatus
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Per-path backend registry (distinct from _memory_connection._backend and
# _user_tier._user_backend). Keyed on the resolved path string so the same
# corpus is one singleton no matter how it is spelled.
# ---------------------------------------------------------------------------

_external_backends: dict[str, SQLiteBackend] = {}
_external_backends_lock = threading.Lock()

# Identity set of backends we constructed as read-only external corpora. Keyed
# on ``id(backend)`` (the backend object is held alive by ``_external_backends``
# for its lifetime, so the id cannot be recycled into a non-external object while
# it remains external). Used by the write-target guard (FR04 / NFR02) without
# mutating the trw-memory ``SQLiteBackend`` instance.
_external_backend_ids: set[int] = set()

# FR03: paths supplied via the ``--memory-db`` startup flag, registered by the
# serve dispatch before the server starts. Unioned with ``config.extra_read_stores``.
_cli_memory_db_paths: list[str] = []


def register_cli_memory_db_paths(paths: list[str] | None) -> None:
    """Record ``--memory-db`` startup paths (FR03).

    Called once by the serve-dispatch path with ``args.memory_db`` (which is
    ``None`` when the flag was not passed). ``None`` / empty is a no-op so the
    NFR01 hot-path stays clean. Idempotent-replace: the latest registration wins
    (a single startup sets this once).
    """
    global _cli_memory_db_paths
    _cli_memory_db_paths = [str(p) for p in (paths or [])]


def reset_external_backends() -> None:
    """Close + discard all external backend singletons and CLI paths (for tests)."""
    global _cli_memory_db_paths
    with _external_backends_lock:
        for backend in _external_backends.values():
            try:
                backend.close()
            except Exception:  # justified: best-effort close in test teardown
                logger.debug("external_backend_close_failed", exc_info=True)
        _external_backends.clear()
        _external_backend_ids.clear()
    _cli_memory_db_paths = []


# ---------------------------------------------------------------------------
# Path resolution (FR01 ∪ FR03, de-dup, exclude default store)
# ---------------------------------------------------------------------------


def resolve_external_store_paths(
    config: TRWConfig,
    *,
    default_db_path: Path | None = None,
) -> list[Path]:
    """Return the effective, de-duped external store paths (FR02/FR03).

    Union of ``config.extra_read_stores`` (FR01) and any ``--memory-db`` CLI
    paths (FR03), each normalized via ``Path.resolve()`` and de-duplicated
    (first occurrence wins, order preserved). The project's own default store
    (``default_db_path``, when supplied) is excluded so an operator who points
    ``--memory-db`` at the live project DB does not double-count records (RISK-02).
    """
    raw: list[str] = [str(p) for p in config.extra_read_stores]
    raw.extend(_cli_memory_db_paths)

    excluded = default_db_path.resolve() if default_db_path is not None else None
    seen: set[Path] = set()
    result: list[Path] = []
    for entry in raw:
        try:
            resolved = Path(entry).resolve()
        except Exception:  # justified: a malformed path string must not break recall
            logger.warning("external_store_skipped", path=entry, reason="unresolvable_path")
            continue
        if resolved in seen:
            continue
        if excluded is not None and resolved == excluded:
            logger.debug("external_store_is_default", path=str(resolved))
            seen.add(resolved)
            continue
        seen.add(resolved)
        result.append(resolved)
    return result


# ---------------------------------------------------------------------------
# Backend construction (read-only, fail-open per path)
# ---------------------------------------------------------------------------


def _has_memories_table(db_path: Path) -> bool:
    """Cheap schema-compat probe (FR04): does the DB carry a ``memories`` table?

    Opens a throwaway ``mode=ro`` SQLite connection (never the trw-memory
    backend) so an incompatible/foreign file is rejected BEFORE we construct a
    backend that might attempt a migration/recovery write on a corpus we do not own.
    """
    import sqlite3

    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memories'").fetchone()
        return row is not None
    finally:
        conn.close()


def _attach_external_backend(path: Path) -> SQLiteBackend | None:
    """Construct (or return the cached) read-only backend for *path*. Fail-open.

    Returns ``None`` (and logs ``external_store_skipped``) when the path is
    missing, not a regular file, schema-incompatible, or otherwise unopenable.
    Never raises (NFR03). The constructed backend is treated as read-only: this
    module never routes a write to it and ``assert_writable_backend`` rejects it.
    """
    key = str(path)
    cached = _external_backends.get(key)
    if cached is not None:
        return cached

    if not path.exists():
        logger.warning("external_store_skipped", path=key, reason="missing")
        return None
    if not path.is_file():
        logger.warning("external_store_skipped", path=key, reason="not_a_regular_file")
        return None
    try:
        if not _has_memories_table(path):
            logger.warning("external_store_skipped", path=key, reason="schema_incompatible")
            return None
    except Exception:  # justified: fail-open — a probe error skips the store, never breaks recall
        logger.warning("external_store_skipped", path=key, reason="schema_incompatible", exc_info=True)
        return None

    try:
        from trw_mcp.state._external_store import _external_backend_kwargs
        from trw_mcp.state._memory_connection import _create_backend

        backend = _create_backend(path, _external_backend_kwargs())
    except Exception:  # justified: fail-open — a foreign corpus must never break recall
        logger.warning("external_store_skipped", path=key, reason="open_failed", exc_info=True)
        return None

    _external_backends[key] = backend
    _external_backend_ids.add(id(backend))
    try:
        record_count = backend.count(namespace=None)
    except Exception:  # justified: count is observability only; degrade to -1
        record_count = -1
    logger.info("external_store_attached", path=key, record_count=record_count)
    return backend


def _external_backend_kwargs() -> dict[str, Any]:
    """Backend kwargs for an external store, mirroring ``_user_backend_kwargs`` (DRY)."""
    from trw_mcp.state._user_tier import _user_backend_kwargs

    return _user_backend_kwargs()


def get_external_backends(
    config: TRWConfig,
    *,
    default_db_path: Path | None = None,
) -> list[SQLiteBackend]:
    """Return one read-only ``SQLiteBackend`` per VALID configured external path (FR02).

    Empty when no external paths are configured (NFR01 hot-path no-op — no
    backend is constructed). Each valid path yields a distinct per-file singleton;
    a missing / non-regular / schema-incompatible path is skipped (FR04). Never
    raises (NFR03).
    """
    paths = resolve_external_store_paths(config, default_db_path=default_db_path)
    if not paths:
        return []
    backends: list[SQLiteBackend] = []
    with _external_backends_lock:
        for path in paths:
            backend = _attach_external_backend(path)
            if backend is not None:
                backends.append(backend)
    return backends


# ---------------------------------------------------------------------------
# Write-target guard (FR04 / NFR02)
# ---------------------------------------------------------------------------


def is_external_backend(backend: SQLiteBackend) -> bool:
    """True iff *backend* is one of our read-only external corpus backends."""
    return id(backend) in _external_backend_ids


def assert_writable_backend(backend: SQLiteBackend) -> None:
    """Raise if a write/prune/consolidate path is about to target an external store.

    FR04 / NFR02: the default project store is the SOLE write destination; an
    external corpus is strictly read-only. Defense-in-depth on top of the fact
    that no external backend is ever passed to a write path.
    """
    if is_external_backend(backend):
        raise PermissionError(
            "refusing to write to an external read-only corpus "
            f"({getattr(backend, '_db_path', '<unknown>')}) — PRD-CORE-202 FR04"
        )


# ---------------------------------------------------------------------------
# Recall federation (union, capped, de-duped, fail-open) — mirrors _user_tier
# ---------------------------------------------------------------------------


def _external_recall_cap() -> int:
    """Per-store cap on external hits (config, default 5). Fails to the default."""
    from trw_mcp.models.config._fields_memory import DEFAULT_EXTERNAL_STORE_RECALL_CAP

    try:
        from trw_mcp.models.config import get_config

        return max(1, int(get_config().external_store_recall_cap))
    except Exception:  # justified: fail-safe to the documented default
        logger.debug("external_store_cap_read_failed", exc_info=True)
        return DEFAULT_EXTERNAL_STORE_RECALL_CAP


def federate_external_stores(
    project_entries: list[MemoryEntry],
    query: str,
    *,
    config: TRWConfig | None = None,
    default_db_path: Path | None = None,
    tags: list[str] | None = None,
    mem_status: MemoryStatus | None = None,
    min_impact: float = 0.0,
    max_results: int = 25,
    is_wildcard: bool = False,
    allow_cold_embedding_init: bool = True,
    as_of: datetime | None = None,
    include_superseded: bool = False,
) -> list[MemoryEntry]:
    """Append capped, de-duped external-corpus hits to the project hits (FR02/FR05).

    Skipped entirely (returns ``project_entries`` unchanged) when no external
    store is configured / valid (NFR01). Never raises -- any failure degrades to
    the input entries (NFR03 fail-open). Each external store contributes at most
    ``external_store_recall_cap`` hits; external hits are appended AFTER project
    hits so the downstream utility re-rank keeps a precise project hit on top
    (tier as a re-rank feature, not an override — mirroring the user tier).
    """
    try:
        if config is None:
            from trw_mcp.models.config import get_config

            config = get_config()
        backends = get_external_backends(config, default_db_path=default_db_path)
        if not backends:
            return project_entries

        cap = _external_recall_cap()
        seen = {e.id for e in project_entries}
        merged = list(project_entries)
        total_added = 0
        for backend in backends:
            hits = _query_external_backend(
                backend,
                query,
                tags=tags,
                mem_status=mem_status,
                min_impact=min_impact,
                max_results=max_results,
                is_wildcard=is_wildcard,
                allow_cold_embedding_init=allow_cold_embedding_init,
                as_of=as_of,
                include_superseded=include_superseded,
            )
            added = 0
            for entry in hits:
                if added >= cap:
                    break
                if entry.id in seen:
                    continue
                seen.add(entry.id)
                merged.append(entry)
                added += 1
            total_added += added
        if total_added:
            logger.debug(
                "recall_federated_external",
                external_hits=total_added,
                project_hits=len(project_entries),
                store_count=len(backends),
            )
        return merged
    except Exception:  # justified: fail-open — external federation must never break recall
        logger.debug("external_tier_federation_failed", exc_info=True)
        return project_entries


def _query_external_backend(
    backend: SQLiteBackend,
    query: str,
    *,
    tags: list[str] | None,
    mem_status: MemoryStatus | None,
    min_impact: float,
    max_results: int,
    is_wildcard: bool,
    allow_cold_embedding_init: bool,
    as_of: datetime | None = None,
    include_superseded: bool = False,
) -> list[MemoryEntry]:
    """Query an external store across ALL namespaces (a corpus may use any ns)."""
    from trw_mcp.state._constants import DEFAULT_LIST_LIMIT

    top_k = max_results if max_results > 0 else DEFAULT_LIST_LIMIT
    if is_wildcard:
        return backend.list_entries(status=mem_status, namespace=None, limit=top_k)
    from trw_mcp.state._memory_queries import _search_entries

    return _search_entries(
        backend,
        query,
        top_k=top_k,
        tags=tags,
        mem_status=mem_status,
        min_impact=min_impact,
        allow_cold_embedding_init=allow_cold_embedding_init,
        namespace=None,
        as_of=as_of,
        include_superseded=include_superseded,
    )
