"""One-time YAML -> SQLite learning-entry migration.

Belongs to the ``_memory_connection.py`` facade. ``ensure_migrated`` is
re-exported there for back-compat (``get_backend`` calls it, and several tests
patch ``trw_mcp.state._memory_connection.ensure_migrated``). Extracted as a
sibling to keep the connection facade under the 350-effective-LOC gate
(PRD-QUAL-110 work pushed it over the boundary).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog
from trw_memory.storage.sqlite_backend import SQLiteBackend

from trw_mcp.state._constants import DEFAULT_NAMESPACE

logger = structlog.get_logger(__name__)

_SENTINEL_NAME = ".migrated"
_NAMESPACE = DEFAULT_NAMESPACE


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
