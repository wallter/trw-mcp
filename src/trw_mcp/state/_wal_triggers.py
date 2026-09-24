"""WAL-checkpoint trigger policy and its persisted clock (PRD-CORE-248 FR04).

Parent facade: :mod:`trw_mcp.state.memory_adapter` (via ``_memory_lookups``),
which owns the checkpoint execution. This module owns only the *decision*, so
the ceremony layer stops holding a storage policy whose state it cannot see.

Three things live here, and they are separate on purpose:

A note on the two size numbers, because they look like drift and are not:
``wal_checkpoint_threshold_mb`` (10 MB default) is the size at which a
checkpoint becomes DUE; trw-memory's ``WAL_JOURNAL_SIZE_LIMIT_BYTES``
(``storage/_connection.py``, 64 MiB) is the size SQLite trims the file back TO
when the WAL next resets -- a truncation target, not a ceiling it enforces on an
active WAL, which can exceed it during a write burst under a long-lived reader. On an engine below 3.51.3 only PASSIVE may run and PASSIVE never
truncates, so a busy store settles around the 64 MiB truncation target with the 10 MB trigger
firing on every evaluation and reclaiming nothing. That is the documented
consequence of the engine gate, not a mis-set knob.

**When is a checkpoint due** — size OR age. The size trigger alone was the whole
policy, and a store below the threshold could go un-checkpointed forever on a
low-write machine. The age trigger is measured against a timestamp persisted
beside the store, so it survives the process restarts a stdio server does
constantly; an absent or unreadable timestamp means "due", which makes the first
run on any existing store checkpoint once rather than inheriting an unknown age.

**What mode may run** — ``PASSIVE`` only, which never resets the WAL and is safe
with any number of concurrent connections. ``TRUNCATE`` needed this process to be
the certified sole live writer; PRD-CORE-298 FR01 deleted the writer locks that
certified it, because the daemon is the one writer.

**How cheap a no-op evaluation is** — NFR01 requires one ``stat`` call and zero
SQLite connections when nothing is due. The trigger therefore reads the WAL size
and the timestamp file and nothing else; the caller opens a connection only
after :func:`evaluate_wal_trigger` says the checkpoint is due.

What is deliberately NOT here: writer pressure never cancels the checkpoint.
That cancel-or-run decision (PRD-INFRA-171 FR06 fixed the same defect for the
journal drain and left this one behind) is gone.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)

__all__ = [
    "CHECKPOINT_ATTEMPT_SUFFIX",
    "CHECKPOINT_EFFECTIVE_SUFFIX",
    "WalTrigger",
    "checkpoint_marker_path",
    "effective_checkpoint_marker_path",
    "evaluate_wal_trigger",
    "last_checkpoint_age_seconds",
    "last_effective_checkpoint_age_seconds",
    "last_reset_checkpoint_age_seconds",
    "record_checkpoint_attempt",
    "record_effective_checkpoint",
    "record_reset_checkpoint",
    "resolve_wal_paths",
]

#: Sidecar holding the epoch seconds of the last checkpoint that RAN without
#: raising — including a ``busy=1`` outcome, which did run. This is the clock
#: the age TRIGGER reads: advancing it on every completed attempt is what stops
#: a store whose readers never release from re-checkpointing on every single
#: evaluation. A plain file beside the store, not a schema change (NFR04
#: forward-only): absence means "due", a torn or unparseable value is treated as
#: absent, and a pre-change process simply never writes it.
CHECKPOINT_ATTEMPT_SUFFIX = ".checkpoint-ts"

#: Sidecar holding the epoch seconds of the last checkpoint that CAUGHT UP WITH
#: THE WAL BACKLOG — ``checkpointed >= log_frames`` and not busy. Answers "is
#: this store keeping up with its own writes?".
CHECKPOINT_EFFECTIVE_SUFFIX = ".checkpoint-effective-ts"

#: Sidecar holding the epoch seconds of the last checkpoint that actually RESET
#: the WAL, i.e. a TRUNCATE that ran. Answers "is this store's WAL ever being
#: reclaimed?" — which is the ``memory_wal`` doctor row's real question, and is
#: NOT answerable from either clock above.
#:
#: This is a THIRD clock rather than a redefinition of the second, and the
#: history is the reason. The effective clock has now been defined three ways,
#: and each redefinition replaced the previous proxy instead of adding the
#: missing measurement:
#:   v1 ``checkpointed > 0``      — frames written back, which PASSIVE does on
#:                                  every run, so the WARN was unreachable.
#:   v2 a file-size decrease      — but SQLite REUSES a fully checkpointed WAL's
#:                                  allocation, so a healthy store looked stalled
#:                                  and the WARN fired forever.
#:   v3 the frame backlog         — honest, and about a different quantity: below
#:                                  SQLite 3.51.3 PASSIVE clears the whole
#:                                  backlog every time, so ``backlog_cleared`` is
#:                                  permanently true on exactly the store that
#:                                  can never reclaim, and the WARN went
#:                                  unreachable again.
#: Reclamation happens on a RESET. Nothing derivable from frames or bytes
#: substitutes for observing one, so it gets its own clock and the backlog clock
#: keeps its own honest meaning.
CHECKPOINT_RESET_SUFFIX = ".checkpoint-reset-ts"

#: How far ahead of "now" a persisted marker may sit before it is treated as
#: unknown rather than fresh. The marker is written with millisecond precision,
#: so a just-written value can read a fraction of a second into the future;
#: anything beyond this is real clock skew or a restored backup, and guessing
#: "very recently checkpointed" from it would suppress the age trigger forever.
_FUTURE_MARKER_TOLERANCE_SECONDS = 1.0


@dataclass(frozen=True)
class WalTrigger:
    """Why (or whether) a checkpoint is due, with the numbers behind it."""

    due: bool
    reason: str
    wal_size_bytes: int
    age_seconds: float | None


def resolve_wal_paths(trw_dir: Path) -> tuple[Path, Path]:
    """Return ``(db_path, wal_path)`` for the project memory store."""
    db_path = trw_dir / "memory" / "memory.db"
    return db_path, db_path.with_suffix(".db-wal")


def checkpoint_marker_path(db_path: Path) -> Path:
    """Return the last-ATTEMPT checkpoint marker path beside *db_path*."""
    return Path(f"{db_path}{CHECKPOINT_ATTEMPT_SUFFIX}")


def effective_checkpoint_marker_path(db_path: Path) -> Path:
    """Return the last-EFFECTIVE checkpoint marker path beside *db_path*."""
    return Path(f"{db_path}{CHECKPOINT_EFFECTIVE_SUFFIX}")


def last_checkpoint_age_seconds(db_path: Path, *, now: float | None = None) -> float | None:
    """Seconds since the last checkpoint ATTEMPT, or ``None`` when unknown.

    ``None`` means "no usable record" — never a fabricated age. Callers treat it
    as due (the trigger) or report ``unknown`` (the doctor row); neither guesses.
    """
    return _marker_age(checkpoint_marker_path(db_path), now=now)


def reset_checkpoint_marker_path(db_path: Path) -> Path:
    """Path of the last-RESET marker beside *db_path*."""
    return db_path.with_name(db_path.name + CHECKPOINT_RESET_SUFFIX)


def last_reset_checkpoint_age_seconds(db_path: Path, *, now: float | None = None) -> float | None:
    """Seconds since the last checkpoint that RESET the WAL, or ``None`` if unknown."""
    return _marker_age(reset_checkpoint_marker_path(db_path), now=now)


def last_effective_checkpoint_age_seconds(db_path: Path, *, now: float | None = None) -> float | None:
    """Seconds since the last checkpoint that CLEARED THE WAL BACKLOG, or None."""
    return _marker_age(effective_checkpoint_marker_path(db_path), now=now)


def _marker_age(marker: Path, *, now: float | None = None) -> float | None:
    """Age in seconds of a timestamp sidecar, or ``None`` when unusable."""
    try:
        raw = marker.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    try:
        recorded = float(raw)
    except ValueError:
        logger.debug("wal_checkpoint_marker_unparseable", path=str(marker))
        return None
    age = (time.time() if now is None else now) - recorded
    # A marker from the future (clock skew, a restored backup) is not evidence
    # of a recent checkpoint; treat it as unknown rather than "very fresh". The
    # tolerance absorbs the millisecond rounding of the persisted value, which
    # can make a just-written marker look a fraction of a second ahead.
    if age < -_FUTURE_MARKER_TOLERANCE_SECONDS:
        return None
    return max(age, 0.0)


def record_checkpoint_attempt(db_path: Path, *, now: float | None = None) -> bool:
    """Persist the last-ATTEMPT timestamp; fail-open. Returns whether it landed.

    Idempotent and last-writer-wins (NFR04). Only ever called after a checkpoint
    that did NOT raise — NFR02 requires a failed checkpoint to leave both clocks
    alone so the age trigger retries on the next evaluation instead of going
    quiet for a full interval.

    The boolean is the point: a marker write that fails leaves the checkpoint age
    unknown, so the age trigger fires again on the very next evaluation and the
    documented hot-loop protection is gone. The caller reports a checkpoint that
    persisted no clock as a PARTIAL success rather than an unqualified one.
    """
    return _write_marker(checkpoint_marker_path(db_path), now=now)


def record_effective_checkpoint(db_path: Path, *, now: float | None = None) -> bool:
    """Persist the last-EFFECTIVE timestamp; fail-open. Returns whether it landed.

    Called only when the checkpoint CAUGHT UP with the WAL backlog. Note what
    this does and does not mean: clearing the backlog is not reclamation, and on
    an engine below SQLite 3.51.3 a store clears its backlog on every run while
    never reclaiming a byte. Reclamation is :func:`record_reset_checkpoint`.
    """
    return _write_marker(effective_checkpoint_marker_path(db_path), now=now)


def record_reset_checkpoint(db_path: Path, *, now: float | None = None) -> bool:
    """Persist the last-RESET timestamp; fail-open. Returns whether it landed.

    Called only when a resetting checkpoint actually ran. This is the only
    observation of reclamation the system makes, so nothing else may write it.
    """
    return _write_marker(reset_checkpoint_marker_path(db_path), now=now)


def _write_marker(marker: Path, *, now: float | None = None) -> bool:
    """Write one timestamp marker. ``True`` when it is on disk, ``False`` on OSError."""
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{time.time() if now is None else now:.3f}\n", encoding="utf-8")
    except OSError as exc:
        logger.warning("wal_checkpoint_marker_write_failed", path=str(marker), error=type(exc).__name__)
        return False
    return True


def evaluate_wal_trigger(trw_dir: Path, config: TRWConfig, *, now: float | None = None) -> WalTrigger:
    """Decide whether a checkpoint is due. Opens no SQLite connection (NFR01).

    Due when the WAL is at or above ``wal_checkpoint_threshold_mb`` **or** the
    last successful checkpoint is at least ``wal_checkpoint_max_age_seconds``
    old. An unknown age counts as due, so a store that has never recorded a
    checkpoint gets one.
    """
    db_path, wal_path = resolve_wal_paths(trw_dir)
    try:
        wal_size = wal_path.stat().st_size
    except OSError:
        return WalTrigger(due=False, reason="no_wal_file", wal_size_bytes=0, age_seconds=None)

    age = last_checkpoint_age_seconds(db_path, now=now)
    over_size = wal_size >= config.wal_checkpoint_threshold_mb * 1024 * 1024
    over_age = age is None or age >= config.wal_checkpoint_max_age_seconds

    if over_size and over_age:
        reason = "size_and_age"
    elif over_size:
        reason = "size"
    elif over_age:
        reason = "age" if age is not None else "never_checkpointed"
    else:
        reason = "not_due"
    return WalTrigger(due=over_size or over_age, reason=reason, wal_size_bytes=wal_size, age_seconds=age)
