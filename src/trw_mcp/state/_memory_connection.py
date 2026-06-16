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
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog

if TYPE_CHECKING:
    from trw_memory.embeddings.local import LocalEmbeddingProvider

from trw_memory.exceptions import CorruptDatabaseUnsalvageableError
from trw_memory.storage.sqlite_backend import SQLiteBackend

from trw_mcp.state import _memory_offline as _offline
from trw_mcp.state._constants import DEFAULT_LIST_LIMIT, DEFAULT_NAMESPACE
from trw_mcp.state._memory_backfill import run_backfill_embeddings
from trw_mcp.state._memory_embedding_status import build_embeddings_status

# PRD-QUAL-110-FR02 gate: ``ensure_migrated`` lives in the ``_memory_migration``
# sibling; re-exported so ``get_backend`` and tests patching
# ``_memory_connection.ensure_migrated`` keep working (bare-name call resolves
# through module globals at call time, so the monkeypatch propagates).
from trw_mcp.state._memory_migration import ensure_migrated as ensure_migrated
from trw_mcp.state._memory_wal_health import _append_wal_health

logger = structlog.get_logger(__name__)

# PRD-QUAL-110-FR04: embeddings offline-switch detection lives in the
# ``_memory_offline`` sibling; re-exported for back-compat (tests + warmup).
_OFFLINE_ENV_VARS = _offline._OFFLINE_ENV_VARS
_embeddings_offline = _offline.embeddings_offline
warmup_suppressed_by_offline = _offline.warmup_suppressed_by_offline

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

# PRD-FIX-COMPOUNDING-3-FR01: Background embeddings backfill thread guard.
# Same pattern as _memory_recovery._RECOVERY_THREAD — prevents concurrent
# backfill races when get_backend() is called multiple times after a recovery.
_BACKFILL_THREAD: threading.Thread | None = None
_BACKFILL_LOCK = threading.Lock()

# Option A+ (council-ratified 2026-06-10): first-recall download warm-up guard.
# With embeddings ON by default, the FIRST trw_recall that allows cold init would
# otherwise pay the all-MiniLM-L6-v2 *download* synchronously on a never-cached
# box, risking an MCP-client timeout on slow networks. `_schedule_embedder_warmup`
# runs the cold `get_embedder()` load on a daemon thread (same single-flight
# pattern as `_BACKFILL_THREAD`), kicked off at session_start, never blocking the
# hot path. Recall degrades to keyword (get_initialized_embedder -> None) until the
# warm-up completes.
_WARMUP_THREAD: threading.Thread | None = None
_WARMUP_LOCK = threading.Lock()

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


def _schedule_post_recovery_backfill(trw_dir: Path, reason: str = "post_recovery") -> bool:
    """Start a background embeddings backfill thread, if one is not already running.

    PRD-FIX-COMPOUNDING-3-FR01: Called from get_backend() when backend.recovered==True
    and from _memory_recovery._recover_and_reset_backend(). Mirrors the
    _RECOVERY_THREAD guard pattern in _memory_recovery.py.

    PRD-FIX-105-FR03: Also called from run_auto_maintenance() with
    reason="low_coverage" when boot-time coverage is below threshold even though
    this boot is not a fresh recovery — the single-flight ``_BACKFILL_LOCK`` guard
    makes the trigger idempotent across both callers. Runs on a daemon thread so it
    never blocks the ``trw_session_start`` hot path.

    Args:
        trw_dir: Path to the ``.trw`` directory.
        reason: Structured-log tag for the trigger ("post_recovery" or "low_coverage").

    Returns:
        True if a new backfill thread was started, False if one was already running.
    """
    global _BACKFILL_THREAD

    def _run_backfill() -> None:
        global _BACKFILL_THREAD
        try:
            backfill_embeddings(trw_dir)
        except Exception:  # justified: fail-open, backfill thread must never crash MCP server
            logger.exception("embeddings_backfill_failed", trw_dir=str(trw_dir))
        finally:
            with _BACKFILL_LOCK:
                if _BACKFILL_THREAD is threading.current_thread():
                    _BACKFILL_THREAD = None

    with _BACKFILL_LOCK:
        if _BACKFILL_THREAD is not None and _BACKFILL_THREAD.is_alive():
            logger.warning(
                "embeddings_backfill_already_running",
                trw_dir=str(trw_dir),
                reason=reason,
            )
            return False
        thread = threading.Thread(
            target=_run_backfill,
            name="trw-embed-backfill",
            daemon=True,
        )
        _BACKFILL_THREAD = thread
        thread.start()

    logger.warning(
        "embeddings_backfill_scheduled",
        trw_dir=str(trw_dir),
        reason=reason,
    )
    return True


def _schedule_embedder_warmup() -> bool:
    """Warm the local embedder on a background daemon thread, if needed.

    Option A+ (council-ratified 2026-06-10, PRD-DIST-254 §FR03 follow-up):
    With ``embeddings_enabled`` now defaulting to True, the FIRST ``trw_recall``
    that passes ``allow_cold_embedding_init=True`` would otherwise pay the
    all-MiniLM-L6-v2 model *download* synchronously on a never-cached box, which
    can exceed an MCP client timeout on slow networks. This kicks off the cold
    :func:`get_embedder` load (which performs the import + download + load) on a
    daemon thread so the download is paid in the background. ``trw_session_start``
    calls this; the hot path itself stays cold-load-free because it uses
    :func:`get_initialized_embedder`, which returns ``None`` (keyword fallback)
    until this warm-up has populated the singleton.

    Single-flight via ``_WARMUP_LOCK`` (mirrors ``_schedule_post_recovery_backfill``).
    No-op (returns ``False``) when embeddings are disabled or the embedder has
    already been initialized.

    Returns:
        True if a new warm-up thread was started, False otherwise.
    """
    global _WARMUP_THREAD

    from trw_mcp.models.config import get_config

    if not get_config().embeddings_enabled:
        return False
    # Already checked/loaded (e.g. an explicit trw_learn embed ran first) — nothing to warm.
    if _embedder_checked:
        return False

    # PRD-QUAL-110-FR04: honor the offline switch (suppress warm-up so no
    # huggingface.co download is attempted at session_start) and otherwise emit
    # the first-run egress-disclosure log line. Delegated to the _memory_offline
    # sibling to keep this facade under the 350-eLOC gate.
    if warmup_suppressed_by_offline(logger):
        return False

    def _run_warmup() -> None:
        global _WARMUP_THREAD
        try:
            # Look the accessor up on the module so tests that patch
            # `_memory_connection.get_embedder` are honored, and so the cold-load
            # path (download + model load) is exercised exactly once.
            get_embedder()
        except Exception:  # justified: fail-open, warm-up thread must never crash the MCP server
            logger.warning("embedder_warmup_failed", exc_info=True)
        finally:
            with _WARMUP_LOCK:
                if _WARMUP_THREAD is threading.current_thread():
                    _WARMUP_THREAD = None

    with _WARMUP_LOCK:
        if _WARMUP_THREAD is not None and _WARMUP_THREAD.is_alive():
            return False
        thread = threading.Thread(
            target=_run_warmup,
            name="trw-embed-warmup",
            daemon=True,
        )
        _WARMUP_THREAD = thread
        thread.start()

    logger.info("embedder_warmup_scheduled")
    return True


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

        # PRD-QUAL-110-FR02: create the memory dir 0700 (it holds the memory.db
        # secret store + sqlite-vec sidecars), consistent with pins.json 0600.
        from trw_mcp.state._paths_permissions import harden_dir_mode, harden_secret_file_mode

        memory_dir = trw_dir / "memory"
        harden_dir_mode(memory_dir, create=True)
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

        # PRD-QUAL-110-FR02: the SQLite store is secret-bearing — 0600 it once
        # the file exists on disk (best-effort; non-POSIX degrades to a WARN).
        harden_secret_file_mode(db_path)

        if backend.recovered:
            # Remove migration sentinel so ensure_migrated re-runs the
            # YAML backfill — restores entries lost from SQLite.
            sentinel = trw_dir / "memory" / _SENTINEL_NAME
            if sentinel.exists():
                sentinel.unlink()
            logger.warning("yaml_backfill_triggered", reason="post_recovery")
            # PRD-FIX-COMPOUNDING-3-FR01: Schedule vector backfill in background.
            # YAML row migration via ensure_migrated() only restores the memories
            # table — sqlite-vec tables remain empty after every recovery.
            _schedule_post_recovery_backfill(trw_dir)
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


def peek_backend() -> SQLiteBackend | None:
    """Return the live singleton backend WITHOUT constructing one.

    Used by maintenance paths (e.g. WAL checkpointing) that must reuse the
    existing connection rather than open a competing one — opening a second
    connection to checkpoint is what triggered the SQLite WAL-reset corruption
    bug. Returns None when no backend has been created yet.

    The unlocked read of ``_backend`` is intentional and safe under CPython
    (reference reads are atomic under the GIL); once set, ``_backend`` is
    stable until the test-only ``reset_backend()``.
    """
    return _backend


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


def get_initialized_embedder() -> LocalEmbeddingProvider | None:
    """Return the cached embedder without triggering a cold model load.

    ``trw_session_start`` is a latency-sensitive MCP hot path.  The normal
    :func:`get_embedder` call may import sentence-transformers and load a local
    model, which can take longer than common MCP client timeouts and can leave
    heavy torch worker threads behind.  Callers that can tolerate keyword-only
    recall should use this helper and skip vector search unless an embedder was
    already initialized by an explicit embedding operation.
    """

    if not _embedder_checked:
        return None
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


def check_embeddings_status(
    *,
    allow_initialize: bool = True,
    coverage_probe: bool = False,
) -> dict[str, object]:
    """Check embedding readiness and return status for session_start advisory."""
    return build_embeddings_status(
        allow_initialize=allow_initialize,
        coverage_probe=coverage_probe,
        embed_failures=_embed_failures,
        embedder_checked=_embedder_checked,
        embedder_unavailable_reason=_embedder_unavailable_reason,
        get_embedder=get_embedder,
        get_initialized_embedder=get_initialized_embedder,
        peek_backend=peek_backend,
        append_wal_health=_append_wal_health,
        logger=logger,
    )


def _embed_and_store_returning(backend: SQLiteBackend, entry_id: str, text: str) -> list[float] | None:
    """Generate + store an embedding and RETURN the vector. Fail-silent.

    PRD-FIX-COMPOUNDING-2 FR02: identical behavior to :func:`_embed_and_store`
    (same single ``embedder.embed`` call, same ``_embed_failures`` increment,
    same vector upsert) but returns the computed vector so the caller can pass
    it to ``schedule_graph_update`` WITHOUT a second embed call. Returns ``None``
    when the embedder is unavailable or the embed call fails.
    """
    global _embed_failures
    embedder = get_embedder()
    if embedder is None:
        # FR07: only count this as a failure when embeddings are EXPECTED
        # (embeddings_enabled=True) but the embedder is unavailable. When
        # embeddings are intentionally disabled, there is no failure to report
        # and incrementing the counter would conflate config with health.
        from trw_mcp.models.config import get_config

        if get_config().embeddings_enabled:
            _embed_failures += 1
        return None
    try:
        vector = embedder.embed(text)
        if vector is not None:
            backend.upsert_vector(entry_id, vector)
        return vector
    except (OSError, ValueError, RuntimeError):
        # justified: embedding is optional enrichment -- store succeeds without it.
        _embed_failures += 1
        logger.debug("embed_and_store_failed", entry_id=entry_id)
        return None


def _embed_and_store(backend: SQLiteBackend, entry_id: str, text: str) -> None:
    """Generate embedding for text and upsert into vector table. Fail-silent.

    FR07 (PRD-FIX-053): Increments ``_embed_failures`` when embedder is
    unavailable or the embed call fails, so ``get_embed_failure_count()``
    can surface this to agents via ``check_embeddings_status()``.

    Thin wrapper over :func:`_embed_and_store_returning` that discards the
    vector — preserves the original ``-> None`` signature for existing callers.
    """
    _embed_and_store_returning(backend, entry_id, text)


# ---------------------------------------------------------------------------
# Migration — ``ensure_migrated`` is imported at module top from the
# ``_memory_migration`` sibling (extracted for the 350-eLOC gate).
# ---------------------------------------------------------------------------


def backfill_embeddings(trw_dir: Path) -> dict[str, int]:
    """Generate embeddings for all entries that don't have one yet."""
    return run_backfill_embeddings(
        trw_dir,
        get_backend=get_backend,
        get_embedder=get_embedder,
        logger=logger,
        namespace=_NAMESPACE,
        max_entries=_MAX_ENTRIES,
    )
