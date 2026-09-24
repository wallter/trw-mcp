"""``trw-mcp doctor`` row for WAL size and checkpoint age (PRD-CORE-248 FR06).

Kept out of ``_subcommands_doctor.py`` (already close to the module-size gate) as
a sibling, the same shape ``_doctor_memory_daemon`` and ``_doctor_embedding_egress``
use.

The check **opens no SQLite connection**, unlike the neighbouring
``memory_backend`` row which probes the backend. A diagnostic that adds a writer
to a contended store is self-defeating: the whole subject of this row is
concurrency pressure, and measuring it must not add to it. Everything reported
here comes from ``stat`` on the WAL file and the checkpoint-timestamp sidecars.

Status mapping, and the reason it never FAILs: a large WAL is a health signal,
not a broken store. It is ``WARN`` only when BOTH the size threshold is exceeded
AND the last checkpoint is older than ``wal_checkpoint_max_age_seconds`` — a big
WAL that was just checkpointed is a busy store working correctly, and warning on
it would train an operator to ignore the row. An age of ``unknown`` (no
timestamp persisted yet, which is every store predating FR04) reports PASS
rather than fabricating an age.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)

__all__ = ["memory_wal_row", "qualifying_interpreters"]

_MIB = 1024 * 1024

#: Interpreters the engine remedy is worth checking for, newest first. This is a
#: named CANDIDATE list, not a search of PATH — the row says so, because claiming
#: "no interpreter on PATH qualifies" would assert a search nobody performed.
_INTERPRETER_CANDIDATES = ("python3.14", "python3.13", "python3.12", "python3")
_PROBE_TIMEOUT_SECONDS = 2.0
_PROBE_BUDGET_SECONDS = 5.0
_PROBE_PROGRAM = "import sqlite3; print(sqlite3.sqlite_version)"


def qualifying_interpreters(
    candidates: tuple[str, ...] = _INTERPRETER_CANDIDATES,
) -> list[tuple[str, str]]:
    """Return ``(name, sqlite_version)`` for candidates whose stdlib clears 3.51.3.

    Read-only and bounded: each candidate gets ``_PROBE_TIMEOUT_SECONDS`` and the
    whole sweep gets ``_PROBE_BUDGET_SECONDS``, after which the rest are skipped.
    A candidate that is absent, times out, exits non-zero, or prints something
    unparseable is simply omitted — a diagnostic row must not raise.

    The probe runs ISOLATED (``-I -S -B``) with stdin closed. A plain ``-c`` run
    puts the current directory first on ``sys.path``, so a repository file named
    sqlite3.py would execute and fabricate the answer. Isolation bounds what the
    probed interpreter imports; it does not make an executable on PATH inert, so
    the list is of NAMED CANDIDATES and the caller reports it as such.
    """
    try:
        from trw_memory.storage._dbapi import wal_reset_safe_version
    except ImportError:  # trw-fail-silent-allow: an absent predicate means the probe cannot be performed, and the caller renders "none of the probed candidates qualified" -- a claim about candidates, never about PATH
        # The installed trw-memory predates the shared predicate (its floor still
        # admits such a release). Report nothing rather than guessing a threshold.
        logger.debug("doctor_wal_interpreter_probe_unavailable")
        return []

    deadline = time.monotonic() + _PROBE_BUDGET_SECONDS
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in candidates:
        if time.monotonic() >= deadline:
            break
        resolved = shutil.which(name)
        if resolved is None or resolved in seen:
            continue
        seen.add(resolved)
        try:
            # S603: argv is fixed, no shell, the executable comes from PATH
            # lookup by design (that is the question being asked), stdin closed.
            completed = subprocess.run(  # noqa: S603
                (resolved, "-I", "-S", "-B", "-c", _PROBE_PROGRAM),
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT_SECONDS,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except (
            OSError,
            subprocess.SubprocessError,
        ):  # trw-fail-silent-allow: a candidate that cannot be spawned or timed out is not a qualifying interpreter, which is the question asked; a diagnostic row must never raise into `trw-mcp doctor`
            continue
        version = completed.stdout.strip()
        if completed.returncode == 0 and wal_reset_safe_version(version):
            found.append((name, version))
    return found


def memory_wal_row(target: Path, config: TRWConfig) -> tuple[str, str]:
    """Return ``(status, message)`` describing the memory store's WAL state.

    Args:
        target: The project root whose ``.trw/memory/memory.db`` is inspected.
        config: The RESOLVED config for *target*. Must not be reconstructed
            here — a bare ``TRWConfig()`` ignores the project's own
            ``.trw/config.yaml``, so the row would measure against a threshold
            the operator did not set.
    """
    from trw_memory.storage._dbapi import backend, is_wal_reset_safe, sqlite_version
    from trw_memory.storage._wal_checkpoint import WAL_RESET_UNSAFE_REMEDY

    from trw_mcp.state._wal_triggers import (
        last_checkpoint_age_seconds,
        last_effective_checkpoint_age_seconds,
        last_reset_checkpoint_age_seconds,
    )

    trw_dir = target / ".trw"
    db_path = trw_dir / "memory" / "memory.db"
    wal_path = db_path.with_suffix(".db-wal")

    try:
        wal_bytes = wal_path.stat().st_size
    except OSError:
        wal_bytes = 0
    wal_mib = wal_bytes / _MIB
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
        f"engine {backend()} {sqlite_version()}, "
        f"WAL {wal_mib:.1f} MiB (checkpoint due at {config.wal_checkpoint_threshold_mb} MiB; "
        f"journal_size_limit asks SQLite to trim it toward 64 MiB when it next resets, "
        f"which is a request, not a hard cap on an active WAL), "
        f"last checkpoint attempt {_age_text(attempt_age)} ago, "
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
        if behind:
            message += (
                " Frames are being left behind by PASSIVE checkpoints, so suspect a reader"
                " holding a snapshot: check wal_checkpoint_complete events for backlog_cleared=false."
            )
        elif not reset_safe:
            message += f" Cause: SQLite {sqlite_version()} -- {WAL_RESET_UNSAFE_REMEDY}."
            # Only this branch pays for the probe: it is the one that prints the
            # engine remedy, and an operator told to change interpreter deserves
            # to be told which ones on this box would do.
            qualifying = qualifying_interpreters()
            if qualifying:
                named = ", ".join(f"{name} (SQLite {version})" for name, version in qualifying)
                message += f" Qualifying interpreters found here: {named}."
            else:
                message += (
                    " None of the probed candidates ("
                    + ", ".join(_INTERPRETER_CANDIDATES)
                    + ") qualified on this PATH."
                )
        else:
            # trw-mcp never requests a resetting checkpoint any more: the writer
            # locks that certified a sole writer went with PRD-CORE-298 FR01, since
            # the daemon is the one writer. So name the way out, not a busy=1.
            message += (
                " Cause: trw-mcp checkpoints a project store PASSIVE only, so SQLite reclaims the"
                " file when the last server holding it exits cleanly; `trw-mcp memory migrate"
                " --to user` moves the store under the daemon."
            )
    logger.debug(
        "doctor_memory_wal",
        wal_bytes=wal_bytes,
        attempt_age=attempt_age,
        effective_age=effective_age,
        wal_reset_safe=reset_safe,
    )
    return status, message


def _age_text(age: float | None) -> str:
    """Render a checkpoint age, or ``unknown`` — never a fabricated number."""
    return "unknown" if age is None else f"{age:.0f}s"
