"""Explicit, bounded embedding repair — the maintenance-only entry point.

Belongs to the ``_memory_connection.py`` facade, which re-exports
``repair_embeddings`` so ``trw_mcp.state._memory_connection.repair_embeddings``
keeps working for the CLI subcommand and the existing tests. Extracted for the
350 effective-LOC gate, following ``_memory_migration.py`` before it: committing
the 2.1.0 train took ``_memory_connection.py`` from 311 to 358 eLOC, and this is
the largest self-contained unit in it.

Deliberately NOT folded into ``_embedding_repair.py`` (which holds ``repair_page``):
that module is imported BY the connection module, so putting an entry point there
that needs ``get_embedder`` would close an import cycle.

**Every name the tests patch is resolved through the parent module at call time.**
``test_embedding_repair.py`` monkeypatches ``connection.get_embedder`` and
``connection.SQLiteBackend`` — including two cases that assert the embedder is
NOT touched before validation — so binding either at import time here would
silently bypass those patches and make the tests pass while testing nothing.
That indirection is load-bearing, not style.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from trw_memory.embeddings.provenance import provider_embedding_space

if TYPE_CHECKING:
    from pathlib import Path

    from trw_memory.storage.interface import EntryCursor

    from trw_mcp.state._embedding_repair import RepairResult

__all__ = ["repair_embeddings"]


def repair_embeddings(trw_dir: Path, *, max_entries: int = 100, after: EntryCursor | None = None) -> RepairResult:
    """Explicit maintenance-only page; no migration, recovery or singleton DB reuse."""
    import sqlite3
    from contextlib import closing

    from trw_memory.models.config import MemoryConfig
    from trw_memory.security.rbac import Permission, require_namespace_permission
    from trw_memory.storage._schema import SCHEMA_VERSION

    from trw_mcp.state import _memory_connection as _conn
    from trw_mcp.state._embedding_repair import repair_page
    from trw_mcp.state._paths import resolve_trw_dir

    if type(max_entries) is not int or not 1 <= max_entries <= 1000:
        raise ValueError("max_entries must be between 1 and 1000")
    target = trw_dir.resolve()
    if target != resolve_trw_dir().resolve():
        raise ValueError("Run embedding repair from the target project so its model configuration is used")
    memory_config = MemoryConfig()
    for permission in (Permission.READ, Permission.WRITE):
        require_namespace_permission(memory_config, "default", permission, "repair_embeddings")
    db_path = target / "memory" / "memory.db"
    # Refuse implicit database creation/schema migration for a vector-only job.
    try:
        with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.Error as exc:
        raise RuntimeError("Cannot read existing memory database for repair") from exc
    if version != SCHEMA_VERSION:
        raise ValueError("Update the memory schema separately before embedding repair")
    provider = _conn.get_embedder()
    space = provider_embedding_space(provider)
    if provider is None or space is None:
        return {
            "status": "blocked",
            "reason": "provider_identity_unavailable",
            "inspected": 0,
            "repaired": 0,
            "skipped": 0,
            "changed": 0,
            "failed": 0,
            "next_cursor": None,
        }
    backend = _conn.SQLiteBackend(db_path, dim=space.dimensions)
    try:
        return repair_page(backend, provider, max_entries=max_entries, after=after)
    finally:
        backend.close()
