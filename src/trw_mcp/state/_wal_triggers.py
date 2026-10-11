"""WAL-checkpoint clocks beside the user memory store, read by the ``memory_wal`` doctor row (PRD-CORE-248 FR06).

The markers are WRITTEN by the daemon's own checkpoint (``trw_memory.storage._wal_checkpoint.stamp_checkpoint_markers``);
this module only reads them. Since PRD-CORE-298 the daemon is the one writer and decides when to checkpoint, so the
trigger that used to live here (``evaluate_wal_trigger``, PRD-CORE-248 FR04, size OR age, with no production caller
left after the CORE-298 move) was deleted on 2026-10-11; what remains is reading the three clocks honestly.

A note on the two size numbers, because they look like drift and are not:
``wal_checkpoint_threshold_mb`` (10 MB default) is the size at which the doctor row calls a WAL oversized;
trw-memory's ``WAL_JOURNAL_SIZE_LIMIT_BYTES`` (``storage/_connection.py``, 64 MiB) is the size SQLite trims the file
back TO when the WAL next resets -- a truncation target, not a ceiling it enforces on an active WAL. On an engine below
3.51.3 only PASSIVE may run and PASSIVE never truncates, so a busy store settles around the 64 MiB truncation target.
That is the documented consequence of the engine gate, not a mis-set knob.

An absent or unreadable marker is "unknown", never a fabricated age; a marker from the future is unknown too.
"""

from __future__ import annotations

import time
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

__all__ = [
    "CHECKPOINT_ATTEMPT_SUFFIX",
    "CHECKPOINT_EFFECTIVE_SUFFIX",
    "checkpoint_marker_path",
    "effective_checkpoint_marker_path",
    "last_checkpoint_age_seconds",
    "last_effective_checkpoint_age_seconds",
    "last_reset_checkpoint_age_seconds",
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
