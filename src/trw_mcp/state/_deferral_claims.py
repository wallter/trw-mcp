"""Single-winner forced-run arbitration for the deferral ledger (PRD-CORE-257 audit row 1/2).

Belongs to the ``deferral_ledger.py`` facade; split out to keep that module
under the 350 effective-LOC gate. Imported lazily by
``deferral_ledger.step_deferral_decision`` (after ``read_ledger``/
``_write_ledger``/``LedgerEntry`` are defined there) to avoid a circular
top-level import — this module needs those names from the parent, and the
parent needs :func:`claim_forced_run` from here.

The prior arbitration lived entirely inside the shared ledger JSON: a
claimant wrote ``running_since_ts`` into the step's entry and re-read the
whole file to check whether it still held the token it wrote. That is a
compare-and-swap built out of two separate file operations with no lock
between them — an execution probe of the production logic returned
``{'A': True, 'B': True}`` for two concurrent claimants, and a contender
whose outer read predated another claim could overwrite the winner's
``running_since_ts`` with its own stale entry. It also returned ``True``
unconditionally for every contender when the ledger read was already
degraded, turning a corrupt-and-unwritable ledger into an N-process herd.

Arbitration now lives in a per-step ``O_CREAT|O_EXCL`` lease file under
``runtime/deferral-claims/``: file *creation* is atomic on every supported
platform, so "exactly one winner" holds independent of the ledger's own read
state. Only the winner ever touches the ledger afterwards, so a loser can
never race the winner's write.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from trw_mcp.state.deferral_ledger import (
    LedgerEntry,
    _now,
    _parse_ts,
    _write_ledger,
    read_ledger,
)


def _claims_dir(trw_dir: Path) -> Path:
    return trw_dir / "runtime" / "deferral-claims"


def _claim_path(trw_dir: Path, step: str) -> Path:
    return _claims_dir(trw_dir) / f"{step}.lock"


def _claim_owner_alive(pid: int) -> bool:
    """Lightweight liveness probe for a claim's owner PID.

    A local copy of ``memory_pressure._pid_is_alive`` rather than an import:
    that module imports ``deferral_ledger`` for :class:`DeferralDecision`, so
    importing it back here would risk a cycle through the parent facade.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:  # trw-fail-silent-allow: the idiomatic liveness check
        return False
    except OSError:
        return True
    return True


def _create_claim(claim_path: Path) -> bool:
    """Atomically create the claim file. True = this process owns it now."""
    try:
        fd = os.open(str(claim_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:  # trw-fail-silent-allow: losing the O_CREAT|O_EXCL race means another process already holds the claim, the expected contended outcome
        return False
    except OSError:
        # trw-fail-silent-allow: cannot arbitrate at all (e.g. unwritable claim
        # dir); running is the same safe-direction fallback as a degraded ledger.
        return True
    try:
        os.write(fd, f"{os.getpid()}\n{_now().isoformat()}\n".encode())
    finally:
        os.close(fd)
    return True


def _reclaim_if_stale(claim_path: Path, *, max_deferral_hours: int) -> None:
    """Unlink a claim whose owner is BOTH past the bound AND dead.

    Age alone cannot tell a slow-but-alive winner from a dead one; liveness
    alone cannot tell a reused pid from the real owner. Best-effort: any
    failure here just means this attempt also loses the race, which is a
    bounded extra deferral, never a wedge (NFR02).
    """
    try:
        lines = claim_path.read_text(encoding="utf-8").splitlines()
    except OSError:  # trw-fail-silent-allow: unreadable claim -> leave it for the next attempt
        return
    if len(lines) < 2:
        return
    try:
        owner_pid = int(lines[0].strip())
    except ValueError:  # trw-fail-silent-allow: malformed claim content -> leave it, not evidence either way
        return
    claimed_at = _parse_ts(lines[1].strip())
    if claimed_at is None or (_now() - claimed_at).total_seconds() < max_deferral_hours * 3600:
        return
    if _claim_owner_alive(owner_pid):
        return
    try:
        claim_path.unlink()
    except OSError:  # trw-fail-silent-allow: another contender already reclaimed it
        pass


def release_claim(trw_dir: Path, step: str) -> None:
    """Release *step*'s claim so a live process can re-win it on its next streak."""
    try:
        _claim_path(trw_dir, step).unlink()
    except OSError:  # trw-fail-silent-allow: nothing to release, or another process already did
        pass


def claim_forced_run(trw_dir: Path, step: str, *, max_deferral_hours: int) -> bool:
    """Try to become the single process that runs an expired *step*.

    Several servers share this ledger — 6 to 8 concurrently on the measured
    boxes — and would otherwise all read the same expired entry and force the
    same expensive step at the same moment, which is exactly the lock-stacking
    the pressure controls exist to prevent. See the module docstring for why
    arbitration is a lease file rather than a ledger-map compare-and-swap.
    """
    claims_dir = _claims_dir(trw_dir)
    try:
        claims_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # trw-fail-silent-allow: cannot arbitrate; running is the safe direction (NFR02)
        return True

    claim_path = _claim_path(trw_dir, step)
    won = _create_claim(claim_path)
    if not won:
        _reclaim_if_stale(claim_path, max_deferral_hours=max_deferral_hours)
        won = _create_claim(claim_path)
    if not won:
        return False

    # Sole writer from here: the claim already excluded every other
    # contender, so this ledger update cannot race a peer's.
    entries, state = read_ledger(trw_dir)
    if state != "degraded":
        current = entries.get(step)
        entries[step] = replace(current or LedgerEntry(step=step), step=step, running_since_ts=_now().isoformat())
        _write_ledger(trw_dir, entries)
    return True


__all__ = ["claim_forced_run", "release_claim"]
