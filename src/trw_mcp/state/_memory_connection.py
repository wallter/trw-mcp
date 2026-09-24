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
from trw_mcp.state._constants import DEFAULT_NAMESPACE
from trw_mcp.state._memory_embedding_status import build_embeddings_status

# PRD-QUAL-110-FR02 gate: ``ensure_migrated`` lives in the ``_memory_migration``
# sibling; re-exported so ``get_backend`` and tests patching
# ``_memory_connection.ensure_migrated`` keep working (bare-name call resolves
# through module globals at call time, so the monkeypatch propagates).
from trw_mcp.state._memory_migration import ensure_migrated as ensure_migrated
from trw_mcp.state._memory_wal_health import _append_wal_health

logger = structlog.get_logger(__name__)

# PRD-QUAL-110-FR04: embeddings offline-switch detection lives in the
# ``_memory_offline`` sibling; re-exported for back-compat.
_OFFLINE_ENV_VARS = _offline._OFFLINE_ENV_VARS
_embeddings_offline = _offline.embeddings_offline

# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_backend: SQLiteBackend | None = None
_backend_lock = threading.Lock()

_embedder: LocalEmbeddingProvider | None = None
_embedder_lock = threading.Lock()
_embedder_checked: bool = False

_embedder_unavailable_reason: str = ""

_SENTINEL_NAME = ".migrated"
_NAMESPACE = DEFAULT_NAMESPACE
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

        # PRD-QUAL-110-FR02: create the memory dir 0700 (it holds the memory.db
        # secret store + sqlite-vec sidecars), consistent with pins.json 0600.
        from trw_mcp.state._paths_permissions import harden_dir_mode, harden_secret_file_mode

        memory_dir = trw_dir / "memory"
        harden_dir_mode(memory_dir, create=True)
        db_path = memory_dir / "memory.db"

        from trw_mcp.models.config import get_config

        cfg = get_config()
        # PRD-INFRA-063: thread B2 knobs from MemoryConfig.
        # Defaults preserve opt-in posture if config load fails.
        backend_kwargs: dict[str, Any] = {"dim": cfg.retrieval_embedding_dim}
        with contextlib.suppress(Exception):
            from trw_memory.models.config import MemoryConfig

            mem_cfg = MemoryConfig()
            backend_kwargs["integrity_check_interval_minutes"] = mem_cfg.memory_integrity_check_interval_minutes
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

        _offline.disclose_download(logger, cfg.retrieval_embedding_model)
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

    Uses the embedder's vectorized ``embed_batch`` so a list of N texts costs a
    single batched inference call instead of N serial ``embed`` calls. Blank
    strings receive ``None`` in the output, matching the per-text ``embed_text``
    contract.
    """
    if not texts:
        return []
    embedder = get_embedder()
    if embedder is None:
        return [None] * len(texts)
    try:
        result: list[list[float] | None] = embedder.embed_batch(texts)
        return result
    except (OSError, ValueError, RuntimeError):
        logger.debug("embed_text_batch_failed", text_count=len(texts))
        return [None] * len(texts)


def check_embeddings_status() -> dict[str, object]:
    """Report whether this install can embed (``update-project``'s warning)."""
    return build_embeddings_status(
        embedder_unavailable_reason=_embedder_unavailable_reason,
        get_embedder=get_embedder,
        append_wal_health=_append_wal_health,
    )


# ---------------------------------------------------------------------------
# Migration — ``ensure_migrated`` is imported at module top from the
# ``_memory_migration`` sibling (extracted for the 350-eLOC gate).
# ---------------------------------------------------------------------------
