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
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)

__all__ = ["memory_wal_row"]

_MIB = 1024 * 1024


def memory_wal_row(target: Path, config: TRWConfig) -> tuple[str, str]:
    """Return ``(status, message)`` describing the memory store's WAL state.

    Args:
        target: The project root whose ``.trw/memory/memory.db`` is inspected.
        config: The RESOLVED config for *target*. Must not be reconstructed
            here — a bare ``TRWConfig()`` ignores the project's own
            ``.trw/config.yaml``, so the row would measure against a threshold
            the operator did not set.
    """
    from trw_memory.storage._dbapi import is_wal_reset_safe, sqlite_version
    from trw_memory.storage._wal_checkpoint import WAL_RESET_UNSAFE_REMEDY

    from trw_mcp.state._wal_triggers import (
        last_checkpoint_age_seconds,
        last_effective_checkpoint_age_seconds,
        last_reset_checkpoint_age_seconds,
    )
    from trw_mcp.state.memory_pressure import live_memory_writer_pids

    trw_dir = target / ".trw"
    db_path = trw_dir / "memory" / "memory.db"
    wal_path = db_path.with_suffix(".db-wal")

    try:
        wal_bytes = wal_path.stat().st_size
    except OSError:
        wal_bytes = 0
    wal_mib = wal_bytes / _MIB
    # Pass the TTL: with pin_ttl_hours=None the heartbeat filter is skipped
    # entirely and a live-but-wedged process keeps inflating "N live writer(s)"
    # forever. An earlier version of this comment claimed "every pressure
    # consumer already passes it"; that was false -- _wal_triggers.sole_live_writer,
    # the function that gates whether TRUNCATE may even be requested, did not,
    # so the fix had reached the observation and not the decision. Both pass it
    # now, which is what keeps this row and that gate counting the same writers.
    writers = live_memory_writer_pids(trw_dir, pin_ttl_hours=config.pin_ttl_hours)
    attempt_age = last_checkpoint_age_seconds(db_path)
    effective_age = last_effective_checkpoint_age_seconds(db_path)
    reset_age = last_reset_checkpoint_age_seconds(db_path)
    reset_safe = is_wal_reset_safe()

    # The WARN reads the EFFECTIVE clock, not the attempt clock. A store on an
    # engine below SQLite 3.51.3 checkpoints on schedule and reclaims nothing,
    # so the attempt clock is always fresh; warning on it would make this row
    # structurally incapable of reporting the one failure it exists to catch.
    #
    # That was the intent from the start, and until 2026-09-10 it did not hold:
    # the effective clock advanced whenever frames were WRITTEN BACK, which
    # PASSIVE does on every run, so `stale` was permanently False and this WARN
    # was unreachable at any WAL size.
    #
    # The first repair overcorrected: it advanced the effective marker only on a
    # file-size DECREASE. But SQLite reuses a fully checkpointed WAL's
    # allocation rather than shrinking it, so a store that cleared its entire
    # backlog looked stalled and this row WARNed forever -- the nuisance alarm
    # the docstring above says trains an operator to ignore the row. The marker
    # now advances when the checkpoint CAUGHT UP WITH THE BACKLOG, measured in
    # frames (`_memory_lookups.maybe_checkpoint_wal`). Do not re-derive this
    # from file size in either direction.
    # Inclusive, matching evaluate_wal_trigger's `>=`. With `>` the trigger
    # fired at exactly the threshold while this row printed "checkpoint due at
    # N MiB" and reported not-oversized (F7).
    oversized = wal_mib >= config.wal_checkpoint_threshold_mb
    # Never checkpointed is UNKNOWN, not stale: an absent marker establishes no
    # elapsed time, and reporting "nothing for over an hour" on a store created
    # a minute ago is a fabricated measurement.
    never_checkpointed = attempt_age is None and effective_age is None
    max_age = config.wal_checkpoint_max_age_seconds
    # TWO ways an oversized WAL is unhealthy, and they are different questions.
    behind = effective_age is None or effective_age > max_age
    unreclaimed = reset_age is None or reset_age > max_age
    # This row's question is RECLAMATION, so `unreclaimed` has to be in the
    # predicate -- and it is the disjunct that was missing all along. The
    # backlog clock alone re-created the original defect: below SQLite 3.51.3
    # PASSIVE clears the whole backlog on every run, so `behind` is permanently
    # False on exactly the store that can never reclaim a byte, and the WARN
    # (which is the only delivery path for WAL_RESET_UNSAFE_REMEDY) went
    # unreachable for the population it exists to serve.
    #
    # A healthy store on a capable engine resets, so `unreclaimed` clears and
    # the row stays quiet -- which is what keeps this from being the nuisance
    # alarm that warning on file size would have been.
    # An unknown age on a small WAL is a store that has simply not needed a
    # checkpoint yet -- PASS, never a fabricated fault (US-004 AC2).
    status = "WARN" if (oversized and (behind or unreclaimed) and not never_checkpointed) else "PASS"

    message = (
        f"WAL {wal_mib:.1f} MiB (checkpoint due at {config.wal_checkpoint_threshold_mb} MiB; "
        f"journal_size_limit asks SQLite to trim it toward 64 MiB when it next resets, "
        f"which is a request, not a hard cap on an active WAL), "
        f"{len(writers)} live writer(s), last checkpoint attempt {_age_text(attempt_age)} ago, "
        f"last checkpoint that CLEARED THE BACKLOG {_age_text(effective_age)} ago, "
        f"last checkpoint that RESET the WAL {_age_text(reset_age)} ago."
    )
    if status == "WARN":
        if behind:
            message += f" Oversized, and no checkpoint has cleared the WAL backlog for over {max_age}s."
        else:
            message += f" Oversized, and the WAL has not been reclaimed for over {max_age}s."
        # Order matters: name the cause of THIS warning. An unsafe engine does
        # not cause a frame backlog -- PASSIVE writes frames back fine -- so
        # leading with the engine remedy on a `behind` store tells an operator
        # to do something that will not clear it.
        if behind and len(writers) <= 1:
            message += (
                " Frames are being left behind with a single live writer, so suspect a reader"
                " holding a snapshot: check wal_checkpoint_complete events for"
                ' truncate_state="busy" or backlog_cleared=false.'
            )
        elif not reset_safe:
            message += f" Cause: SQLite {sqlite_version()} -- {WAL_RESET_UNSAFE_REMEDY}."
        elif len(writers) > 1:
            # Name the cause that actually applies here. The engine-capable
            # branch below used to be the only one, and it sent an operator
            # hunting a busy=1 that CANNOT occur with peers present: with more
            # than one live writer no process certifies sole ownership, so a
            # resetting checkpoint is never requested, the run is PASSIVE, and a
            # healthy PASSIVE returns busy=0. Advice pointing at evidence that
            # structurally cannot appear is worse than no advice.
            message += (
                f" Cause: {len(writers)} live writers, so no process can certify sole ownership"
                " and a resetting checkpoint is never requested. SQLite reclaims the file when"
                " the last server holding the store exits cleanly."
            )
        else:
            # NOT "check for busy=1": on the sole-writer path a busy TRUNCATE is
            # retried as PASSIVE on the same connection and the retry's busy=0
            # OVERWRITES it, so the field this used to name reads 0 on exactly
            # the store this branch describes. Name the fields that do vary.
            message += (
                " The engine can reset the WAL and this is the only live writer, so suspect a"
                " reader holding a snapshot: check wal_checkpoint_complete events for"
                ' truncate_state="busy" or backlog_cleared=false.'
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
