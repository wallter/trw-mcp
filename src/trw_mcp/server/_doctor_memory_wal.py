"""``trw-mcp doctor`` row for WAL size, live writers and checkpoint age (PRD-CORE-248 FR06).

Kept out of ``_subcommands_doctor.py`` (already close to the module-size gate) as
a sibling, the same shape ``_doctor_memory_daemon`` and ``_doctor_embedding_egress``
use.

The check **opens no SQLite connection**, unlike the neighbouring
``memory_backend`` row which probes the backend. A diagnostic that adds a writer
to a contended store is self-defeating: the whole subject of this row is
concurrency pressure, and measuring it must not add to it. Everything reported
here comes from ``stat`` on the WAL file, the writer-registry lock files, and the
checkpoint-timestamp sidecar.

Status mapping, and the reason it never FAILs: a large WAL is a health signal,
not a broken store. It is ``WARN`` only when BOTH the size threshold is exceeded
AND the last checkpoint is older than ``wal_checkpoint_max_age_seconds`` — a big
WAL that was just checkpointed is a busy store working correctly, and warning on
it would train an operator to ignore the row. An age of ``unknown`` (no
timestamp persisted yet, which is every store predating FR04) reports PASS
rather than fabricating an age.
"""

from __future__ import annotations

from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

__all__ = ["memory_wal_row"]

_MIB = 1024 * 1024


def memory_wal_row(target: Path) -> tuple[str, str]:
    """Return ``(status, message)`` describing the memory store's WAL state.

    Args:
        target: The project root whose ``.trw/memory/memory.db`` is inspected.
    """
    from trw_memory.storage._dbapi import is_wal_reset_safe, sqlite_version
    from trw_memory.storage._wal_checkpoint import WAL_RESET_UNSAFE_REMEDY

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state._wal_triggers import (
        last_checkpoint_age_seconds,
        last_effective_checkpoint_age_seconds,
    )
    from trw_mcp.state.memory_pressure import live_memory_writer_pids

    config = TRWConfig()
    trw_dir = target / ".trw"
    db_path = trw_dir / "memory" / "memory.db"
    wal_path = db_path.with_suffix(".db-wal")

    try:
        wal_bytes = wal_path.stat().st_size
    except OSError:
        wal_bytes = 0
    wal_mib = wal_bytes / _MIB
    writers = live_memory_writer_pids(trw_dir)
    attempt_age = last_checkpoint_age_seconds(db_path)
    effective_age = last_effective_checkpoint_age_seconds(db_path)
    reset_safe = is_wal_reset_safe()

    # The WARN reads the EFFECTIVE clock, not the attempt clock. A store on an
    # engine below SQLite 3.51.3 checkpoints on schedule and reclaims nothing,
    # so the attempt clock is always fresh; warning on it would make this row
    # structurally incapable of reporting the one failure it exists to catch.
    oversized = wal_mib > config.wal_checkpoint_threshold_mb
    stale = effective_age is None or effective_age > config.wal_checkpoint_max_age_seconds
    # An unknown effective age on a small WAL is a store that has simply not
    # needed a checkpoint yet -- PASS, never a fabricated fault (US-004 AC2).
    status = "WARN" if (oversized and stale) else "PASS"

    message = (
        f"WAL {wal_mib:.1f} MiB (checkpoint due at {config.wal_checkpoint_threshold_mb} MiB; "
        f"SQLite caps the file at 64 MiB via journal_size_limit), "
        f"{len(writers)} live writer(s), last checkpoint attempt {_age_text(attempt_age)} ago, "
        f"last EFFECTIVE checkpoint {_age_text(effective_age)} ago."
    )
    if status == "WARN":
        message += f" Oversized and nothing reclaimed for over {config.wal_checkpoint_max_age_seconds}s."
        if not reset_safe:
            message += f" Cause: SQLite {sqlite_version()} -- {WAL_RESET_UNSAFE_REMEDY}."
        else:
            message += (
                " The engine can reset the WAL, so suspect a long-lived reader holding pages:"
                " check wal_checkpoint_complete events for a persistent busy=1."
            )
    logger.debug(
        "doctor_memory_wal",
        wal_bytes=wal_bytes,
        writers=len(writers),
        attempt_age=attempt_age,
        effective_age=effective_age,
        wal_reset_safe=reset_safe,
    )
    return status, message


def _age_text(age: float | None) -> str:
    """Render a checkpoint age, or ``unknown`` — never a fabricated number."""
    return "unknown" if age is None else f"{age:.0f}s"
