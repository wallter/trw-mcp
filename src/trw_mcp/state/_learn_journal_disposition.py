"""Terminal disposition for one replayed learn-journal record.

Belongs to the ``state/learn_journal.py`` facade. Answers the two questions the
drain driver cannot answer from a returned status string alone.

**1. Was anything actually consumed?** The pre-fix driver booked every non-
``"error"`` status as ``recovered``. ``execute_learn`` returns ``"rejected"``
(not ``"error"``) when a write-time accept gate refuses the content — and it
returns BEFORE any ``consume_journal``. So a gate-rejected replay left the
pending file on disk while the sweep reported a recovery, ``pending_count``
never fell, and ``learn-drain`` exited 0 with ``pending_after ==
pending_before``. The ground truth is the file, not the string: a record that
is still on disk was NOT recovered, whatever the replay said.

**2. May this record be retried?** The gates can legitimately be STRICTER at
replay time than they were at journal time — ``llm_utility_filter_enabled``
flipped on, or tightened length caps / injection patterns / noise heuristics
shipped by an upgrade. The journal is designed to survive restarts and
upgrades, so this is an upgrade hazard by construction, and the pre-fix answer
("retry forever") meant such a record occupied active journal capacity and was
re-attempted on every sweep for the lifetime of the project.

The policy, therefore:

* **deterministic refusal** (content policy, noise filter, PII/enum/schema
  ``ValueError``) — the next attempt is guaranteed to fail identically, so do
  NOT retry. The record moves ASIDE to a dead-letter directory with the reason
  recorded, keeping the operator's data while freeing the active journal.
* **transient failure** (backend unavailable, DB lock, timeout) — retry, but
  with a BUDGET. After ``max_attempts`` the record dead-letters too, so no
  outcome is "forever".
* Nothing is ever deleted. Dead-letter records keep the full original journal
  shape, so moving one back into ``pending/`` re-arms it after a fix.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Final, Literal, NamedTuple

import structlog
from trw_memory.exceptions import PIIBlockError, PoisoningError, SchemaValidationError

from trw_mcp.state._learn_journal_io import fsync_dir, read_record, write_record_atomic

logger = structlog.get_logger(__name__)

# Sibling of ``pending/`` under the learnings dir. Deliberately NOT a
# subdirectory of ``pending/``: ``pending_count`` and the replay scan glob
# ``pending/*.json``, and a dead-lettered record must stop counting as pending
# the moment it moves — that count is what "did the journal actually drain?"
# is measured on.
DEAD_LETTER_DIRNAME: Final = "dead_letter"

# Terminal statuses ``execute_learn`` returns for a DETERMINISTIC refusal of the
# content itself. ``"error"`` is deliberately absent — a store error is the
# transient case the write-ahead journal exists to retry.
DETERMINISTIC_STATUSES: Final[frozenset[str]] = frozenset({"rejected", "invalid"})

# Exception types that mean "this payload can never be replayed as-is":
# invalid enum values, schema/shape violations (incl. ``Utf8ValidationError``,
# a ``SchemaValidationError`` subclass), PII/poisoning content refusals — the
# payload itself is what fails these trw-memory checks, so replaying the
# identical bytes fails identically every time. Narrow on purpose — a broader
# net (AttributeError, RuntimeError) would dead-letter records failing on a
# CODE bug that a later upgrade fixes, and trw-memory's TRANSIENT exceptions
# (StorageError, MemoryConnectionError, StaleConnectionError, RateLimitError)
# and MemoryQuarantinedError (holds the entry for human review rather than
# refusing it — a later approval makes replay meaningless, not
# deterministic-fail) deliberately stay off this list.
DETERMINISTIC_EXCEPTIONS: Final[tuple[type[BaseException], ...]] = (
    ValueError,
    TypeError,
    SchemaValidationError,
    PIIBlockError,
    PoisoningError,
)

ReplayOutcome = Literal["recovered", "dead_lettered", "retained"]


class ReplayDisposition(NamedTuple):
    """What the drain must do with a record it just replayed."""

    outcome: ReplayOutcome
    reason: str


def dead_letter_dir(trw_dir: Path, learnings_dir: str = "learnings") -> Path:
    """Directory holding records that will never be replayed again."""
    return trw_dir / learnings_dir / DEAD_LETTER_DIRNAME


def classify_replay(
    *,
    consumed: bool,
    status: str,
    error: BaseException | None,
    attempt: int,
    max_attempts: int,
) -> ReplayDisposition:
    """Decide the terminal disposition of one replay attempt.

    Args:
        consumed: Whether the pending record is gone from disk — the ground
            truth for "this replay reached a terminal outcome". Only a consumed
            record may be booked as ``recovered``.
        status: Terminal status string the replay returned ("" when it raised).
        error: Exception the replay raised, if any.
        attempt: 1-based index of THIS attempt (prior attempts + 1).
        max_attempts: Retry budget for transient failures; ``0`` disables the
            budget (retain indefinitely — the config-only rollback), which is
            why the deterministic branches are checked first and are NOT
            budget-gated.
    """
    if consumed:
        return ReplayDisposition("recovered", "")
    if error is not None:
        if isinstance(error, DETERMINISTIC_EXCEPTIONS):
            return ReplayDisposition("dead_lettered", f"deterministic_error:{type(error).__name__}")
    elif status in DETERMINISTIC_STATUSES:
        return ReplayDisposition("dead_lettered", f"deterministic_rejection:{status}")
    if 0 < max_attempts <= attempt:
        return ReplayDisposition("dead_lettered", f"retry_budget_exhausted:{attempt}")
    return ReplayDisposition("retained", "")


def dead_letter(
    path: Path,
    *,
    target_dir: Path,
    reason: str,
    status: str,
    error: str,
    attempt: int,
) -> bool:
    """Move a never-replayable record aside; return whether the move landed.

    Copy-then-unlink, never the reverse: a crash between the two duplicates the
    record (the next sweep rewrites the same dead-letter name idempotently),
    whereas unlink-first would lose it outright. A ``False`` return means the
    record is STILL pending — the caller must count it as retained rather than
    report a move that did not happen.
    """
    record = read_record(path) or {}
    record["dead_letter"] = {
        "reason": reason,
        "status": status,
        "error": error,
        "attempts": attempt,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if not write_record_atomic(target_dir / path.name, record):
        return False
    try:
        path.unlink(missing_ok=True)
        fsync_dir(path.parent)
    except OSError:  # justified: fail-open, an un-unlinkable record stays pending rather than being lost
        logger.warning("learn_journal_dead_letter_unlink_failed", path=str(path), exc_info=True)
        return False
    logger.warning(
        "learn_journal_record_dead_lettered",
        path=str(path),
        reason=reason,
        status=status,
        attempts=attempt,
    )
    return True


def record_attempt(path: Path, record: dict[str, object], attempt: int) -> None:
    """Persist the replay-attempt count so the retry budget survives restarts.

    Rewritten in place with the ORIGINAL mtime preserved — the age escape hatch
    and FIFO replay order are both keyed on mtime, so a bump would reset a
    record's age on every failed sweep and defeat the eventual-drain guarantee.
    """
    record["attempts"] = attempt
    write_record_atomic(path, record, preserve_mtime=True)


__all__ = [
    "DEAD_LETTER_DIRNAME",
    "DETERMINISTIC_EXCEPTIONS",
    "DETERMINISTIC_STATUSES",
    "ReplayDisposition",
    "ReplayOutcome",
    "classify_replay",
    "dead_letter",
    "dead_letter_dir",
    "record_attempt",
]
