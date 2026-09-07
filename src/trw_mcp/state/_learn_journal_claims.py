"""Atomic cross-process claims for learn-journal work units.

Belongs to ``state/learn_journal.py`` (per-record replay claims) and to the
``tools/_learn_journal_background.py`` continuation (the one-time batch-dedup
migration claim).

**The defect this closes.** ``drain_pending`` materialises the pending list and
then replays each record, and nothing between those two steps says "this record
is mine". The pre-existing argument for why that was safe — whichever drain
consumes the file first wins, and the other's ``_iter_pending_records`` simply
never yields it — only covers SEQUENTIAL arrival. Two stdio server processes
(the repository's normal architecture), or one process whose next session_start
sweep overlaps the background continuation of the previous one, both materialise
the SAME list and both enter ``execute_learn`` for the same id. The same shape
applies to the batch-dedup migration: every process independently stats the same
absent marker, so two can run the quadratic scan against the same sidecars.

**The mechanism.** One claim file per work unit, created atomically. The content
is written to a temp file FIRST and ``os.link``-ed into place, so a claim file is
never observed half-written: link(2) either creates the name or fails with
``EEXIST``, and there is no window in which the name exists without its owner
recorded. (``os.link`` is unavailable on a few exotic filesystems; the O_EXCL
fallback keeps the atomicity and only re-opens the half-written window, which the
staleness rule then treats as unowned.)

**Reclaiming.** A claim naming a dead PID is stale and is reclaimed, so a crash
mid-replay cannot strand a record forever. PID reuse is rejected the same way the
writer census rejects it: the claim records the owner's process birth epoch, and
an owner whose birth epoch is materially LATER than the recorded one is a
different process wearing a recycled PID (a "ghost") and its claim is stale. An
owner whose identity cannot be verified at all is assumed LIVE — refusing to
replay costs one deferred record, replaying twice costs a duplicated row.

**Claims are never load-bearing for durability.** A failure to acquire only
defers work; a failure to release only defers it until the next process death.
The pending record itself is untouched, so ``drain_pending``'s
classification-on-file semantics are exactly what they were.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import structlog

from trw_mcp.state._writer_census_identity import (
    _BIRTH_EPOCH_SLACK_SECONDS,
    process_birth_epoch,
)
from trw_mcp.state.memory_pressure import _pid_is_alive

logger = structlog.get_logger(__name__)

#: Suffix appended to the claimed path. Deliberately NOT ``.json`` — the pending
#: scan globs ``*.json``, so a claim must not be mistaken for a record.
CLAIM_SUFFIX = ".claim"

__all__ = ["CLAIM_SUFFIX", "Claim", "acquire_claim", "claim_path_for", "release_claim"]


@dataclass(frozen=True)
class Claim:
    """A held claim. Pass it to :func:`release_claim` in a ``finally``."""

    path: Path
    target: Path


def claim_path_for(target: Path) -> Path:
    """The claim file that guards *target*."""
    return target.with_name(target.name + CLAIM_SUFFIX)


def _owner_record() -> dict[str, object]:
    pid = os.getpid()
    return {"pid": pid, "epoch": process_birth_epoch(pid), "claimed_at": time.time()}


def _is_stale(path: Path) -> bool:
    """Is the claim at *path* abandoned by its owner?

    True only when the owner is PROVABLY gone: a dead PID, or a live PID whose
    process birth epoch post-dates the claim (PID reuse). An unreadable or
    unowned claim is also stale — :func:`acquire_claim` publishes the content
    atomically, so a claim with no legible owner was not written by this code
    path and holding work behind it forever would be a new stall mode.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # justified: an illegible claim names no owner
        logger.warning("learn_journal_claim_unreadable", path=str(path), outcome="reclaimable")
        return True
    if not isinstance(data, dict):
        return True
    pid = data.get("pid")
    if not isinstance(pid, int) or not _pid_is_alive(pid):
        return True
    recorded = data.get("epoch")
    birth = process_birth_epoch(pid)
    if not isinstance(recorded, (int, float)) or birth is None:
        # Identity unverifiable: assume the owner is live. Deferring one record
        # is cheap; replaying one twice is the defect this module exists to fix.
        return False
    return bool(birth > float(recorded) + _BIRTH_EPOCH_SLACK_SECONDS)


def _publish(claim: Path, payload: bytes) -> bool:
    """Create *claim* atomically. False means someone else already holds it."""
    tmp = claim.with_name(f"{claim.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(payload)
    except OSError as exc:  # fail-open: an unwritable claim dir means "no claim"
        logger.warning("learn_journal_claim_write_failed", path=str(claim), reason=str(exc), exc_info=True)
        return False
    try:
        os.link(tmp, claim)
        return True
    except FileExistsError:  # trw-fail-silent-allow: losing the atomic link race means another process already holds the claim, the expected contended outcome
        return False
    except (OSError, NotImplementedError):
        # link(2) unsupported here — fall back to O_EXCL, which is still atomic
        # for the NAME even though the content lands a syscall later.
        try:
            fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:  # trw-fail-silent-allow: losing the O_CREAT|O_EXCL race means another process already holds the claim, the expected contended outcome
            return False
        except OSError as exc:  # fail-open: claiming is best-effort
            logger.warning("learn_journal_claim_open_failed", path=str(claim), reason=str(exc), exc_info=True)
            return False
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)
        return True
    finally:
        tmp.unlink(missing_ok=True)


def acquire_claim(target: Path) -> Claim | None:
    """Claim *target* for this process, or return ``None`` if another holds it.

    Retries exactly once, and only after reclaiming a claim whose owner is
    provably gone, so a crashed drain never strands a record permanently and a
    live one is never raced.
    """
    claim = claim_path_for(target)
    payload = json.dumps(_owner_record()).encode("utf-8")
    for attempt in (1, 2):
        if _publish(claim, payload):
            return Claim(path=claim, target=target)
        if attempt == 2 or not _is_stale(claim):
            logger.debug("learn_journal_claim_held", path=str(claim))
            return None
        try:
            claim.unlink(missing_ok=True)
        except OSError as exc:  # fail-open: a claim we cannot clear is simply held
            logger.warning("learn_journal_claim_reclaim_failed", path=str(claim), reason=str(exc), exc_info=True)
            return None
        logger.info("learn_journal_claim_reclaimed", path=str(claim), reason="owner_gone")
    return None


def release_claim(claim: Claim | None) -> None:
    """Drop a held claim. Idempotent and fail-open."""
    if claim is None:
        return
    try:
        claim.path.unlink(missing_ok=True)
    except OSError as exc:  # fail-open: a leaked claim is reclaimed by the staleness rule
        logger.warning("learn_journal_claim_release_failed", path=str(claim.path), reason=str(exc), exc_info=True)
