"""``trw-mcp doctor`` row for a leftover pre-fix per-namespace ``warm.db`` (learning L-LhQe).

Before the trw-memory tier-root fix, a namespace's tier files could be
scattered under the wrong root when a set ``memory_single_store_path``
diverged from ``storage_path``. trw-memory does not copy, move, or delete a
leftover: the warm cache rebuilds itself from the canonical backend the next
time that namespace's tier is used, so an orphaned ``warm.db`` is simply
unused, not lost data -- this row's remedy for one is "you may delete it".

It ALSO flags a stranded cold-tier archive (a namespace's ``entries/``
directory left behind at the old root). That IS data -- rows not yet visible
to the current tier runtime -- so the remedy is to move or copy it onto the
namespace's correct directory, never to delete it.

Read-only in both cases: this row inspects the filesystem and never writes,
moves, or deletes anything itself.

Codex r2 (must-fix, still honoured): a healthy, fully-migrated store (where
``storage_path`` and the single store's own directory already coincide, the
normal daemon layout) must never have its ACTIVE per-namespace directories
mistaken for legacy orphans. This row calls trw-memory's own
:func:`trw_memory.lifecycle.tiers._legacy_warm_migration.legacy_tier_dirs` --
the single source of truth for which directories could possibly be legacy --
rather than re-deriving that mapping here.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

__all__ = ["memory_warm_legacy_row"]


def memory_warm_legacy_row(target: Path, config: TRWConfig) -> tuple[str, str]:
    """Return ``(status, message)`` naming a leftover legacy per-namespace ``warm.db``, if any.

    Args:
        target: The project root (unused -- this row inspects the machine-local
            daemon store, same as ``memory_wal_row``, kept for doctor-row
            signature parity).
        config: The resolved trw-mcp config (unused directly; kept for doctor-row
            signature parity -- trw-memory's own ``MemoryConfig`` is what decides
            whether a single store is even in play).
    """
    del target, config
    from trw_memory.lifecycle.tiers._legacy_warm_migration import legacy_tier_dirs
    from trw_memory.models.config import MemoryConfig
    from trw_memory.user_paths import resolve_user_memory_dir

    ambient = MemoryConfig()  # reads the real, ambient memory_single_store_path
    if not ambient.memory_single_store_path:
        return "PASS", "no memory_single_store_path is configured; the per-namespace layout applies as designed."

    # This row's scope has always been the daemon's own machine-local store
    # (same as memory_wal_row, never a per-project one) -- pin storage_path to
    # it explicitly rather than reading it from the ambient environment, so a
    # caller's shell (which does not run inside the daemon's own process) still
    # reproduces the pre-fix per-namespace root the daemon itself would use.
    memory_config = MemoryConfig(
        storage_path=str(resolve_user_memory_dir(create=False)),
        memory_single_store_path=ambient.memory_single_store_path,
    )

    orphan_warm: list[Path] = []
    stranded_cold: list[tuple[Path, Path]] = []
    for legacy_dir, target_dir in legacy_tier_dirs(memory_config):
        warm_db = legacy_dir / "memory" / "warm.db"
        if warm_db.exists():
            orphan_warm.append(warm_db)
        cold_entries = legacy_dir / "entries"
        if cold_entries.is_dir():
            stranded_cold.append((cold_entries, target_dir / "entries"))

    if not orphan_warm and not stranded_cold:
        return "PASS", "no leftover legacy per-namespace warm.db or stranded cold archive found."

    parts = [
        f"the warm cache rebuilds automatically from the canonical store; the old warm.db at "
        f"{warm_db} is unused, and you may delete it"
        for warm_db in orphan_warm
    ]
    parts.extend(
        f"archived entries not yet visible to the current tier runtime at {legacy_dir}; move or "
        f"copy it to {target_dir} -- never delete it, it is data"
        for legacy_dir, target_dir in stranded_cold
    )
    return "WARN", "; ".join(parts) + "."
