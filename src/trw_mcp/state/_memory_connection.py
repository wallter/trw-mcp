"""Connection management for the trw-memory SQLite backend and embedding provider.

Owns the module-level singletons (_backend, _embedder), their thread-safe
initialization, teardown, and one-time YAML-to-SQLite migration.

This module is an internal implementation detail of ``memory_adapter.py``.
External code should import from ``memory_adapter`` (the public facade).

Note: ``get_config`` is imported inside function bodies (late binding) to
avoid circular imports with the config module.  ``migrate_entries_dir`` is
likewise late-imported from ``trw_memory.migration.from_trw``.  All
embedder/embed helpers call sibling functions defined in this module directly.
"""

from __future__ import annotations

import contextlib
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog

if TYPE_CHECKING:
    from trw_memory.embeddings.local import LocalEmbeddingProvider

from trw_memory.exceptions import CorruptDatabaseUnsalvageableError
from trw_memory.storage.sqlite_backend import SQLiteBackend

from trw_mcp.state._constants import DEFAULT_LIST_LIMIT, DEFAULT_NAMESPACE

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_backend: SQLiteBackend | None = None
_backend_lock = threading.Lock()

_embedder: LocalEmbeddingProvider | None = None
_embedder_lock = threading.Lock()
_embedder_checked: bool = False

# FR07 (PRD-FIX-053): Embed failure counter -- resets on process restart.
_embed_failures: int = 0
_embedder_unavailable_reason: str = ""

_SENTINEL_NAME = ".migrated"
_NAMESPACE = DEFAULT_NAMESPACE
_MAX_ENTRIES = DEFAULT_LIST_LIMIT
_CORRUPTION_MARKERS = (
    "malformed",
    "database disk image",
    "not a database",
    "file is not a database",
)


def _is_corruption_error(exc: BaseException) -> bool:
    """Return True when *exc* looks like SQLite corruption."""
    if isinstance(exc, CorruptDatabaseUnsalvageableError):
        return False
    message = str(exc).lower()
    return any(marker in message for marker in _CORRUPTION_MARKERS)


def _log_terminal_recovery(db_path: Path, exc: CorruptDatabaseUnsalvageableError) -> None:
    """Log strict recovery refusal before surfacing it to the caller."""
    logger.error(
        "memory_recovery_terminal",
        db=str(db_path),
        backup_path=exc.backup_path,
        action="raise",
    )


def _create_backend(db_path: Path, backend_kwargs: dict[str, Any]) -> SQLiteBackend:
    """Instantiate ``SQLiteBackend`` with a compatibility fallback for tests.

    Some tests monkeypatch ``SQLiteBackend`` with tiny fakes that only accept
    ``(db_path, dim=None)``. Production backends support the extra kwargs, so we
    retry without them only when constructor shape is the limiting factor.
    """
    try:
        return SQLiteBackend(db_path, **backend_kwargs)
    except TypeError as exc:
        if "unexpected keyword argument" not in str(exc):
            raise
        return SQLiteBackend(db_path, dim=cast("int", backend_kwargs["dim"]))


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
            return _backend  # pragma: no cover -- race guard

        if trw_dir is None:
            from trw_mcp.state._paths import resolve_trw_dir

            trw_dir = resolve_trw_dir()

        memory_dir = trw_dir / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        db_path = memory_dir / "memory.db"

        from trw_mcp.models.config import get_config

        cfg = get_config()
        # PRD-INFRA-063/064: thread B2/B3 knobs from MemoryConfig.
        # Defaults preserve opt-in posture if config load fails.
        backend_kwargs: dict[str, Any] = {"dim": cfg.retrieval_embedding_dim}
        with contextlib.suppress(Exception):
            from trw_memory.models.config import MemoryConfig

            mem_cfg = MemoryConfig()
            backend_kwargs["integrity_check_interval_minutes"] = mem_cfg.memory_integrity_check_interval_minutes
            backend_kwargs["concurrent_writer_warn_threshold"] = mem_cfg.memory_concurrent_writer_warn_threshold
        try:
            backend = _create_backend(db_path, backend_kwargs)
        except CorruptDatabaseUnsalvageableError as exc:
            _log_terminal_recovery(db_path, exc)
            raise
        except Exception as exc:  # justified: boundary, retry recovery only for SQLite corruption on backend init
            if not _is_corruption_error(exc):
                logger.exception("backend_init_failed", db=str(db_path), action="raise")
                raise
            # If constructor fails even after internal recovery attempt,
            # force-recover and retry once for corruption-like failures.
            logger.warning("backend_init_retry_after_corruption", db=str(db_path), exc_info=True)
            if db_path.exists():
                try:
                    conn = SQLiteBackend.recover_db(db_path)
                except CorruptDatabaseUnsalvageableError as recover_exc:
                    _log_terminal_recovery(db_path, recover_exc)
                    raise
                conn.close()
            try:
                backend = _create_backend(db_path, backend_kwargs)
            except CorruptDatabaseUnsalvageableError as retry_exc:
                _log_terminal_recovery(db_path, retry_exc)
                raise

        if backend.recovered:
            # Remove migration sentinel so ensure_migrated re-runs the
            # YAML backfill — restores entries lost from SQLite.
            sentinel = trw_dir / "memory" / _SENTINEL_NAME
            if sentinel.exists():
                sentinel.unlink()
            logger.info("yaml_backfill_triggered", reason="post_recovery")
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


def get_embedder() -> LocalEmbeddingProvider | None:
    """Return the singleton LocalEmbeddingProvider, or None if unavailable.

    Only attempts initialization when ``embeddings_enabled=True`` in config.
    The result is cached -- repeated calls are cheap.
    """
    global _embedder, _embedder_checked, _embedder_unavailable_reason
    if _embedder_checked:
        return _embedder

    with _embedder_lock:
        if _embedder_checked:
            return _embedder  # pragma: no cover -- race guard

        from trw_mcp.models.config import get_config

        cfg = get_config()
        if not cfg.embeddings_enabled:
            _embedder_unavailable_reason = ""
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
                _embedder_unavailable_reason = ""
                logger.info(
                    "embedder_initialized",
                    model=cfg.retrieval_embedding_model,
                    dim=cfg.retrieval_embedding_dim,
                )
            else:
                _embedder_unavailable_reason = provider.unavailable_reason() or (
                    "sentence-transformers is not installed"
                )
                logger.info(
                    "embeddings_enabled_but_unavailable",
                    reason=_embedder_unavailable_reason,
                    hint="pip install trw-memory[embeddings]",
                )
        except Exception:  # justified: import-guard, embedder init may fail if deps missing
            # FR06: Log at warning so embedder failures are visible in logs.
            # Do NOT set _embedder_checked -- allows retry on next call or
            # after reset_embedder() (e.g. session restart).
            logger.warning("embedder_init_failed", exc_info=True)
            return _embedder

        _embedder_checked = True
        return _embedder


def reset_embedder() -> None:
    """Reset the embedder singleton (for tests)."""
    global _embedder, _embedder_checked, _embedder_unavailable_reason
    with _embedder_lock:
        _embedder = None
        _embedder_checked = False
        _embedder_unavailable_reason = ""


def embedding_available() -> bool:
    """Return True if an embedding provider is available (PRD-CORE-080)."""
    return get_embedder() is not None


def embed_text(text: str) -> list[float] | None:
    """Generate a single embedding vector for *text* (PRD-CORE-080).

    Returns None if embeddings are unavailable or text is empty.
    """
    embedder = get_embedder()
    if embedder is None or not text.strip():
        return None
    try:
        result: list[float] | None = embedder.embed(text)
        return result
    except (OSError, ValueError, RuntimeError):
        logger.debug("embed_text_failed", text_length=len(text))
        return None


def embed_text_batch(texts: list[str]) -> list[list[float] | None]:
    """Generate embeddings for multiple texts (PRD-CORE-080).

    Returns a list of embedding vectors (or None per text on failure).
    """
    if not texts:
        return []
    embedder = get_embedder()
    if embedder is None:
        return [None] * len(texts)
    try:
        return [embed_text(t) for t in texts]
    except (OSError, ValueError, RuntimeError):
        logger.debug("embed_text_batch_failed", text_count=len(texts))
        return [None] * len(texts)


def get_embed_failure_count() -> int:
    """Return the number of embed failures since process start (FR07).

    This counter increments each time ``_embed_and_store()`` finds no embedder
    or encounters an error. It resets on process restart and is not persisted.
    """
    return _embed_failures


def reset_embed_failure_count() -> None:
    """Reset the embed failure counter to zero (for tests)."""
    global _embed_failures
    _embed_failures = 0


def _resolve_memory_db_path() -> Path:
    """Resolve the memory.db path from the .trw directory.

    Returns the primary SQLite store path (``memory.db``, distinct from
    ``vectors.db`` used for embeddings). Internal helper for WAL health
    reporting — avoids circular imports.
    """
    from trw_mcp.state._paths import resolve_trw_dir

    return resolve_trw_dir() / "memory" / "memory.db"


def _append_wal_health(result: dict[str, object]) -> None:
    """Append WAL file size advisory to an embeddings status result dict.

    PRD-QUAL-050-FR06: When the WAL file exceeds the configured threshold,
    adds ``wal_size_mb`` and ``wal_advisory`` keys to the result dict.
    Fail-open: exceptions are silently caught.
    """
    try:
        from trw_mcp.models.config import get_config as _get_config

        cfg = _get_config()
        db_path = _resolve_memory_db_path()
        wal_path = db_path.with_suffix(".db-wal")
        if wal_path.exists():
            wal_size_mb = wal_path.stat().st_size / (1024 * 1024)
            if wal_size_mb > cfg.wal_checkpoint_threshold_mb:
                result["wal_size_mb"] = round(wal_size_mb, 1)
                result["wal_advisory"] = (
                    f"WAL file is {wal_size_mb:.1f}MB (threshold: {cfg.wal_checkpoint_threshold_mb}MB)"
                )
    except Exception:  # justified: fail-open, WAL health is advisory only
        logger.debug("wal_health_check_failed", exc_info=True)


def check_embeddings_status() -> dict[str, object]:
    """Check embedding readiness and return status for session_start advisory.

    Returns a dict with:
    - ``enabled``: whether config has embeddings_enabled=True
    - ``available``: whether deps are installed and model loads
    - ``advisory``: human-readable message (empty when everything is fine)
    - ``recent_failures``: count of embed failures since process start (FR07)
    - ``wal_size_mb``: (optional) WAL file size when above threshold (FR06)
    - ``wal_advisory``: (optional) human-readable WAL size warning (FR06)
    """
    from trw_mcp.models.config import get_config

    cfg = get_config()
    if not cfg.embeddings_enabled:
        result: dict[str, object] = {
            "enabled": False,
            "available": False,
            "advisory": "",
            "recent_failures": _embed_failures,
        }
        _append_wal_health(result)
        return result

    embedder = get_embedder()
    if embedder is not None:
        result = {
            "enabled": True,
            "available": True,
            "advisory": "",
            "recent_failures": _embed_failures,
        }
        _append_wal_health(result)
        return result

    reason = _embedder_unavailable_reason or "sentence-transformers is not installed"
    result = {
        "enabled": True,
        "available": False,
        "advisory": f"Embeddings enabled but unavailable: {reason}. Run: pip install trw-memory[embeddings]",
        "recent_failures": _embed_failures,
    }
    _append_wal_health(result)
    return result


def _embed_and_store(backend: SQLiteBackend, entry_id: str, text: str) -> None:
    """Generate embedding for text and upsert into vector table. Fail-silent.

    FR07 (PRD-FIX-053): Increments ``_embed_failures`` when embedder is
    unavailable or the embed call fails, so ``get_embed_failure_count()``
    can surface this to agents via ``check_embeddings_status()``.
    """
    global _embed_failures
    embedder = get_embedder()
    if embedder is None:
        _embed_failures += 1
        return
    try:
        vector = embedder.embed(text)
        if vector is not None:
            backend.upsert_vector(entry_id, vector)
    except (OSError, ValueError, RuntimeError):
        # justified: embedding is optional enrichment -- store succeeds without it.
        _embed_failures += 1
        logger.debug("embed_and_store_failed", entry_id=entry_id)


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def ensure_migrated(trw_dir: Path, backend: SQLiteBackend) -> dict[str, int]:
    """One-time migration of YAML learning entries into SQLite.

    Idempotent: writes a sentinel file on success; subsequent calls are no-ops.
    Individual entry failures are logged and skipped -- never aborts the batch.

    Args:
        trw_dir: Path to the ``.trw`` directory.
        backend: Active :class:`SQLiteBackend` to store entries in.

    Returns:
        Dict with ``migrated`` and ``skipped`` counts.
    """
    sentinel = trw_dir / "memory" / _SENTINEL_NAME
    if sentinel.exists():
        return {"migrated": 0, "skipped": 0}

    from trw_mcp.models.config import get_config

    cfg = get_config()
    entries_dir = trw_dir / cfg.learnings_dir / cfg.entries_dir
    if not entries_dir.exists():
        # Fresh project -- nothing to migrate
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("migrated_at=" + datetime.now(timezone.utc).isoformat())
        return {"migrated": 0, "skipped": 0}

    migrated = 0
    skipped = 0

    try:
        from trw_memory.migration.from_trw import migrate_entries_dir

        memory_entries = migrate_entries_dir(entries_dir)
    except Exception:  # justified: boundary, migration from YAML entries may fail on corrupt files
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
        except Exception:  # per-item error handling: one bad entry must not abort migration
            skipped += 1
            logger.warning(
                "memory_migration_entry_skipped",
                exc_info=True,
                entry_id=entry.id,
            )

    # Only write sentinel on success
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(
        f"migrated_at={datetime.now(timezone.utc).isoformat()}\nmigrated={migrated}\nskipped={skipped}\n"
    )

    logger.info(
        "memory_migration_complete",
        migrated=migrated,
        skipped=skipped,
    )
    return {"migrated": migrated, "skipped": skipped}


def backfill_embeddings(trw_dir: Path) -> dict[str, int]:
    """Generate embeddings for all entries that don't have one yet.

    Called on first activation of embeddings (session_start with
    embeddings_enabled=True and deps available). Idempotent -- skips
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
        if entry.metadata.get("system_canary") == "true":
            continue
        # Check if vector already exists by attempting a search
        # with high top_k -- cheaper than adding a get_vector method
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
