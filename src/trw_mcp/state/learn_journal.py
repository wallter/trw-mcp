"""Durable write-ahead journal for accepted learnings.

Belongs to the ``trw_learn`` durability path (``tools/_learn_impl.py``).

**Why this exists.** ``execute_learn`` performs its first durable write
(``store_learning`` → SQLite) only AFTER a slow pre-store pipeline: loading the
full active set and running semantic dedup, whose embedding model cold-start on
the first learn of a session can push the call past the MCP client's 120s tool
timeout. The client then "backgrounds" the call; if the session EXITS before the
synchronous pipeline reaches the store, the accepted learning is NEVER persisted
— a SILENT data loss.

**The fix.** An ACCEPTED learning (one that has passed the noise + content-policy
gates) is journaled here BEFORE the slow work runs, with an fsync so it survives
a mid-call process kill. The record is consumed only after a confirmed terminal
outcome (stored / deduped / quarantined). If the process dies in between,
:func:`drain_pending` replays the record on the next ``trw_session_start``
maintenance sweep — where dedup (now with a warm embedder) collapses it against
the row a partial earlier attempt may already have written, so the learning
lands EXACTLY ONCE.

One JSON file per pending record under ``.trw/learnings/pending/<id>.json``.
Append is an atomic ``tmp`` + :func:`os.replace` with an ``fsync`` of both the
file and its directory (see ``_learn_journal_io``). Consume is an ``unlink``.
All operations are fail-open: a broken journal must never block a learn (it only
forfeits the durability guard for that one call, which is logged).

**No record is replayed forever.** A replay that consumes nothing is never
booked as a recovery, and one that can never succeed — a deterministic accept-
gate refusal, an invalid enum, or a transient failure past its retry budget —
moves to ``.trw/learnings/dead_letter/<id>.json`` with the reason recorded
rather than occupying active journal capacity. That policy lives in
``_learn_journal_disposition``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path

import structlog
from typing_extensions import TypedDict

from trw_mcp.state._learn_journal_disposition import (
    DEAD_LETTER_DIRNAME as DEAD_LETTER_DIRNAME,
)
from trw_mcp.state._learn_journal_disposition import (
    classify_replay,
    dead_letter,
    record_attempt,
)
from trw_mcp.state._learn_journal_disposition import (
    dead_letter_dir as dead_letter_dir,
)
from trw_mcp.state._learn_journal_io import (
    JOURNAL_RECORD_VERSION as JOURNAL_RECORD_VERSION,
)
from trw_mcp.state._learn_journal_io import (
    fsync_dir,
    read_record,
    write_record_atomic,
)

logger = structlog.get_logger(__name__)


class LearnJournalDrainResult(TypedDict, total=False):
    """Outcome counts for one :func:`drain_pending` sweep.

    Emitted into the session_start maintenance payload ONLY when at least one
    record was pending, so the common (empty) path costs zero response tokens.

    ``recovered`` counts ONLY records whose pending file is gone — a replay that
    consumed nothing is never a recovery. ``dead_lettered`` counts records moved
    aside as unreplayable; ``retained`` counts records still queued for a later
    sweep. The three are disjoint and, with ``deferred``, account for every
    pending record.
    """

    pending: int
    replayed: int
    recovered: int
    dead_lettered: int
    retained: int
    deferred: int


def pending_dir(trw_dir: Path, learnings_dir: str = "learnings") -> Path:
    """Directory that holds one JSON file per un-consumed accepted learning."""
    return trw_dir / learnings_dir / "pending"


def _record_path(trw_dir: Path, learning_id: str, learnings_dir: str) -> Path:
    return pending_dir(trw_dir, learnings_dir) / f"{learning_id}.json"


def journal_pending(
    trw_dir: Path,
    learning_id: str,
    payload: dict[str, object],
    *,
    learnings_dir: str = "learnings",
) -> Path | None:
    """Durably record an accepted learning before the slow pre-store pipeline.

    Writes ``pending/<learning_id>.json`` via an atomic tmp + ``os.replace`` and
    fsyncs the file and its directory. Returns the record path, or ``None`` if
    journaling failed (fail-open — the caller proceeds without the durability
    guard rather than losing the learn outright).

    Args:
        trw_dir: The resolved ``.trw`` directory.
        learning_id: Stable id; keying the file on it makes replay idempotent.
        payload: The original ``execute_learn`` args needed to faithfully
            re-run the learn on replay.
        learnings_dir: Config-driven learnings dir name (default ``learnings``).
    """
    record: dict[str, object] = {
        "version": JOURNAL_RECORD_VERSION,
        "learning_id": learning_id,
        "payload": payload,
    }
    target = _record_path(trw_dir, learning_id, learnings_dir)
    if not write_record_atomic(target, record):
        logger.warning("learn_journal_write_failed", learning_id=learning_id)
        return None
    logger.debug("learn_journal_pending_written", learning_id=learning_id)
    return target


def consume_pending(trw_dir: Path, learning_id: str, *, learnings_dir: str = "learnings") -> None:
    """Remove a pending record after a confirmed terminal outcome.

    Called once the learning is durable (stored) or was intentionally handled
    (deduped / quarantined). Fail-open and idempotent: unlinking an
    already-absent record is a no-op.
    """
    try:
        target = _record_path(trw_dir, learning_id, learnings_dir)
        target.unlink(missing_ok=True)
        fsync_dir(target.parent)
    except OSError:  # justified: fail-open, a stuck consume must not fail the learn
        logger.warning("learn_journal_consume_failed", learning_id=learning_id, exc_info=True)


def _iter_pending_records(trw_dir: Path, learnings_dir: str) -> Iterator[tuple[Path, str, dict[str, object]]]:
    """Yield ``(path, learning_id, record)`` for each REPLAYABLE pending record.

    Shared scan behind :func:`iter_pending` and :func:`aged_pending_count` so
    "replayable" means exactly one thing: a poison record (unknown version,
    corrupt body, malformed shape) is skipped by BOTH, which is what stops the
    age escape hatch from spinning on a record replay can never consume.

    Yields the WHOLE record (not just its payload) because the drain rewrites it
    to persist the replay-attempt count that bounds the retry budget.
    """
    directory = pending_dir(trw_dir, learnings_dir)
    if not directory.is_dir():
        return
    files = [p for p in directory.glob("*.json") if p.is_file()]
    for path in sorted(files, key=lambda p: p.stat().st_mtime):
        record = read_record(path)
        if record is None:
            continue
        if record.get("version") != JOURNAL_RECORD_VERSION:
            logger.warning("learn_journal_record_unknown_version", path=str(path))
            continue
        payload = record.get("payload")
        learning_id = record.get("learning_id")
        if not isinstance(payload, dict) or not isinstance(learning_id, str):
            logger.warning("learn_journal_record_malformed", path=str(path))
            continue
        yield path, learning_id, record


def _record_payload(record: dict[str, object]) -> dict[str, object]:
    payload = record.get("payload")
    return payload if isinstance(payload, dict) else {}


def _record_attempts(record: dict[str, object]) -> int:
    """Prior failed replay attempts persisted on the record (0 when absent)."""
    raw = record.get("attempts")
    return raw if isinstance(raw, int) and raw > 0 else 0


def iter_pending(trw_dir: Path, *, learnings_dir: str = "learnings") -> Iterator[tuple[str, dict[str, object]]]:
    """Yield ``(learning_id, payload)`` for each replayable pending record.

    Records with an unknown schema version or a corrupt body are skipped (left
    on disk, not consumed) so a poison record cannot silently discard a real
    one; oldest-first by mtime so recovery is roughly FIFO.
    """
    for _path, learning_id, record in _iter_pending_records(trw_dir, learnings_dir):
        yield learning_id, _record_payload(record)


def aged_pending_count(
    trw_dir: Path,
    *,
    max_age_seconds: float,
    learnings_dir: str = "learnings",
    now: float | None = None,
) -> int:
    """Count replayable records whose mtime age has reached *max_age_seconds*.

    PRD-INFRA-171-FR06 (a). The bound is measured from the record's mtime — the
    field :func:`iter_pending` already sorts on — and is INCLUSIVE: a record
    whose age exactly equals the bound is counted (and therefore drained), one
    second younger is not.

    ``max_age_seconds <= 0`` disables the escape hatch and returns 0. Fail-open:
    a record whose mtime cannot be read is not counted rather than raising.
    """
    if max_age_seconds <= 0:
        return 0
    reference = time.time() if now is None else now
    aged = 0
    for path, _learning_id, _payload in _iter_pending_records(trw_dir, learnings_dir):
        try:
            mtime = path.stat().st_mtime
        except OSError:  # justified: fail-open, an unstattable record is simply not aged
            continue
        if reference - mtime >= max_age_seconds:
            aged += 1
    return aged


def pressure_drain_budget(
    trw_dir: Path,
    *,
    drain_limit: int,
    min_batch: int,
    max_age_seconds: float,
    learnings_dir: str = "learnings",
) -> int:
    """Records the sweep may replay WHILE writer pressure is engaged.

    PRD-INFRA-171-FR06 (a) + (b). The pre-FR06 answer was always zero, which is
    why 42 records were journaled and none ever drained: with the pressure
    threshold at 2, a single peer MCP instance deferred every sweep forever.

    The deferral's intent — recovery must not fight a live writer for the memory
    backend — is preserved by making the under-pressure sweep SMALLER, not
    absent:

    * ``min_batch`` is the minimum-progress floor, clamped to ``drain_limit - 1``
      so it is always STRICTLY less than an unpressured sweep. ``0`` restores the
      pre-FR06 defer-always behaviour (the config-only P0 rollback).
    * the count of records at or past ``max_age_seconds`` raises the budget when
      the floor alone would leave an aged record stranded — eventual drain is
      then a guarantee rather than a wait for a quiet moment.

    The result is capped at ``drain_limit`` so a large accumulated backlog cannot
    turn one pressured sweep into an unbounded one.
    """
    floor = max(0, min(min_batch, drain_limit - 1))
    aged = aged_pending_count(
        trw_dir,
        max_age_seconds=max_age_seconds,
        learnings_dir=learnings_dir,
    )
    return min(drain_limit, max(floor, aged))


def pending_count(trw_dir: Path, *, learnings_dir: str = "learnings") -> int:
    """Number of pending record files on disk (cheap directory listing)."""
    directory = pending_dir(trw_dir, learnings_dir)
    if not directory.is_dir():
        return 0
    return sum(1 for p in directory.glob("*.json") if p.is_file())


def drain_pending(
    trw_dir: Path,
    replay_fn: Callable[[str, dict[str, object]], str],
    *,
    limit: int,
    learnings_dir: str = "learnings",
    max_attempts: int = 0,
) -> LearnJournalDrainResult:
    """Replay un-consumed pending records through ``replay_fn``.

    ``replay_fn(learning_id, payload)`` must persist the learning (idempotently,
    keyed on ``learning_id``) and return its terminal status string. It owns
    consuming its own journal file on a durable/handled outcome via
    :func:`consume_pending` (see ``_learn_impl.execute_learn``); this driver
    orchestrates, CLASSIFIES, and counts, so the consume rule lives in exactly
    one place.

    Classification is on the FILE, not the returned string: whether the pending
    record is still on disk is the only honest answer to "did this replay reach
    a terminal outcome?". A record still on disk is never counted as
    ``recovered`` — see :mod:`trw_mcp.state._learn_journal_disposition` for why
    the string alone once produced false recoveries that never drained.

    An un-consumed record is then either DEAD-LETTERED (a deterministic refusal
    that would fail identically forever, or a transient failure that exhausted
    ``max_attempts``) or RETAINED for the next sweep with its attempt count
    persisted. ``max_attempts=0`` disables the budget, so only deterministic
    refusals move aside. At most ``limit`` records are attempted per sweep.

    Returns per-outcome counts (see :class:`LearnJournalDrainResult`), or an
    empty dict when nothing was pending.
    """
    result: LearnJournalDrainResult = {}
    pending = list(_iter_pending_records(trw_dir, learnings_dir))
    if not pending:
        return result

    recovered = 0
    dead_lettered = 0
    retained = 0
    attempted = 0
    dead_dir = dead_letter_dir(trw_dir, learnings_dir)
    for path, learning_id, record in pending:
        if attempted >= limit:
            break
        attempted += 1
        attempt = _record_attempts(record) + 1
        status = ""
        error: BaseException | None = None
        try:
            status = replay_fn(learning_id, _record_payload(record))
        except Exception as exc:  # justified: fail-open, one bad replay must not abort the sweep or the session
            logger.warning("learn_journal_replay_failed", learning_id=learning_id, exc_info=True)
            error = exc
        disposition = classify_replay(
            consumed=not path.exists(),
            status=status,
            error=error,
            attempt=attempt,
            max_attempts=max_attempts,
        )
        if disposition.outcome == "recovered":
            recovered += 1
            continue
        moved = disposition.outcome == "dead_lettered" and dead_letter(
            path,
            target_dir=dead_dir,
            reason=disposition.reason,
            status=status,
            error="" if error is None else f"{type(error).__name__}: {error}",
            attempt=attempt,
        )
        if moved:
            dead_lettered += 1
            continue
        # Still pending — either a deliberate retry or a dead-letter move that
        # failed. Persist the attempt so the budget survives a restart.
        record_attempt(path, record, attempt)
        retained += 1

    result["pending"] = len(pending)
    result["replayed"] = attempted
    if recovered:
        result["recovered"] = recovered
    if dead_lettered:
        result["dead_lettered"] = dead_lettered
    if retained:
        result["retained"] = retained
    deferred = len(pending) - attempted
    if deferred > 0:
        result["deferred"] = deferred
    logger.info(
        "learn_journal_drain",
        pending=len(pending),
        replayed=attempted,
        recovered=recovered,
        dead_lettered=dead_lettered,
        retained=retained,
        deferred=deferred,
    )
    return result
