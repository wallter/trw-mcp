"""User-space (machine-local) memory backend registry -- PRD-CORE-185 FR02.

Today ``_memory_connection.get_backend()`` returns a single project-local
``SQLiteBackend`` singleton rooted at ``<trw_dir>/memory/memory.db``. This
module adds a SECOND, additive singleton -- ``get_user_backend()`` -- rooted at
``resolve_user_memory_dir()`` (machine-local, shared box-wide across every repo).

DRY: backend construction (config-derived kwargs, the test-compat
``_create_backend`` shim, corruption recovery, and ``ensure_migrated``) is
REUSED from ``_memory_connection`` rather than duplicated. The user backend is
a distinct file, so a separate per-file singleton is safe under the
single-connection / WAL-reset discipline (two stores, two files, two
connections is fine; what is forbidden is two connections to the SAME db).

This is a focused sibling of ``_memory_connection.py`` (which is over the 350
effective-LOC gate); all user-tier backend logic lives here (NFR07).
"""

from __future__ import annotations

import contextlib
import threading
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from trw_memory.storage.sqlite_backend import SQLiteBackend

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level singleton (distinct from _memory_connection._backend)
# ---------------------------------------------------------------------------

_user_backend: SQLiteBackend | None = None
_user_backend_lock = threading.Lock()


def _user_backend_kwargs() -> dict[str, Any]:
    """Build backend kwargs from config, mirroring ``get_backend`` (DRY).

    Defaults preserve an opt-in posture if config load fails, exactly as the
    project store does.
    """
    from trw_mcp.models.config import get_config

    cfg = get_config()
    backend_kwargs: dict[str, Any] = {"dim": cfg.retrieval_embedding_dim}
    with contextlib.suppress(Exception):
        from trw_memory.models.config import MemoryConfig

        mem_cfg = MemoryConfig()
        backend_kwargs["integrity_check_interval_minutes"] = mem_cfg.memory_integrity_check_interval_minutes
        backend_kwargs["concurrent_writer_warn_threshold"] = mem_cfg.memory_concurrent_writer_warn_threshold
    return backend_kwargs


def get_user_backend() -> SQLiteBackend:
    """Return the machine-local user-space ``SQLiteBackend`` singleton.

    Lazily constructs the backend on first call at
    ``resolve_user_memory_dir()/memory.db`` (the resolver creates the parent
    dir). The singleton is keyed implicitly on the user-home path so it is
    shared across all repos on the box. The project ``get_backend()`` is
    unchanged and additive to this one.

    Returns:
        Shared user-space :class:`SQLiteBackend` instance.
    """
    global _user_backend
    if _user_backend is not None:
        return _user_backend

    with _user_backend_lock:
        if _user_backend is not None:
            return _user_backend  # pragma: no cover -- race guard

        from trw_mcp.state._memory_connection import _create_backend, ensure_migrated
        from trw_mcp.state._user_paths import resolve_user_memory_dir

        memory_dir = resolve_user_memory_dir()
        db_path = memory_dir / "memory.db"
        backend_kwargs = _user_backend_kwargs()

        backend = _create_backend(db_path, backend_kwargs)

        # ensure_migrated keys the sentinel on ``<trw_dir>/memory``; pass the
        # parent so the sentinel lands at ``<memory_dir>/.migrated``. The user
        # base has no ``learnings/entries`` dir, so this is a clean no-op that
        # only writes the sentinel (no project YAML is migrated into the user
        # store).
        ensure_migrated(memory_dir.parent, backend)

        _user_backend = backend
        logger.info("user_backend_initialized", db=str(db_path), outcome="created")
        return _user_backend


def peek_user_backend() -> SQLiteBackend | None:
    """Return the live user backend WITHOUT constructing one (mirrors peek_backend).

    Used by gating paths (e.g. federation skip-when-absent) that must not pay
    the construction cost. Returns None when no user backend has been created.
    """
    return _user_backend


def reset_user_backend() -> None:
    """Close and discard the user-backend singleton (for tests)."""
    global _user_backend
    with _user_backend_lock:
        if _user_backend is not None:
            _user_backend.close()
            _user_backend = None
    # core185-8: the user-scope presence probe is memoized on a boot-time
    # condition; clearing the backend (test isolation / reconfiguration) must
    # also re-arm that probe so a later config/disk change is observed.
    from trw_mcp.state._tier_routing import reset_user_scope_cache

    reset_user_scope_cache()
