"""Bounded per-step deferral ledger for writer-pressure skips (PRD-CORE-257-FR03).

Deferral under a condition that never clears is silent cancellation. Before this
module no session-start step recorded when it last ran, so on the measured boxes
embeddings backfill, stale-run close, the auto-upgrade check, the learn-journal
drain and the session-start side effects had not run in any deferred session and
nothing said for how long.

The ledger is a small fail-open JSON file in the runtime directory already used
for pin state, following the read-coerce-write shape of ``_feedback_nudge.py``.
It maps each covered step to ``last_completed_ts``, ``deferred_since_ts``,
``running_since_ts`` and ``deferred_count``; when a deferral streak reaches
``session_start_max_deferral_hours`` the step runs despite pressure.

Two distinctions carry the design:

**Cold start is not a degraded read.** A *missing file*, or a clean file with no
entry for this step, is a cold start: the step opens a fresh streak, is deferred,
and ``ledger_state`` stays ``ok``. An absent entry is never treated as infinitely
old, so first contact cannot force six steps to run at once.

**A lost ledger runs the work.** A file that exists but cannot be trusted —
unreadable, truncated, non-JSON, wrong-shaped, or one whose last write failed —
is a degraded read. Every covered step is then treated as expired and RUNS.
Treating a lost ledger as "everything is a fresh streak" silently restarts every
bound and re-creates the unbounded deferral this module exists to remove; running
is the honest direction and is cheap, since the worst case per step is the single
30 s SQLite busy-timeout wait already budgeted.

Security (NFR03): only step names from the fixed six-value set, non-negative
integers and UTC timestamps are ever written. No PIDs, learning ids, queries or
free text, so the file carries nothing sensitive if copied. It is created inside
the runtime directory whose 0600 posture is already managed.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import structlog

logger = structlog.get_logger(__name__)

# Clock-skew allowance for ledger timestamps (audit row 3): a stored
# timestamp more than this far ahead of "now" cannot have been written by a
# clock that agrees with ours and is treated as corrupt, never as a healthy
# age of 0.0. Same order of magnitude as
# ``_writer_census_identity._FUTURE_HEARTBEAT_SKEW_SECONDS`` — not a policy
# threshold, so NFR05's "no magic numbers" rule does not apply.
_MAX_FUTURE_SKEW_SECONDS = 300.0

# Latch window for the "degraded" read verdict (audit row 2): a corrupt file
# repaired by ONE step's ``record_completion`` mid-pass must not read as
# healthy for the remaining covered steps in the SAME ``trw_session_start``.
# A single session-start call completes in well under a second; 5s is a
# generous bound on one pass without persisting the latch into the next,
# unrelated session start.
_DEGRADED_LATCH_SECONDS = 5.0
_degraded_since: dict[Path, float] = {}
_degraded_lock = threading.Lock()

COVERED_STEPS = frozenset(
    {
        "auto_upgrade_check",
        "stale_runs",
        "embeddings_backfill",
        "pending_learns",
        "side_effects",
        "nudges",
    }
)
"""The six session-start steps that writer pressure may skip."""

StepOutcome = Literal["executed", "deferred", "expired_ran", "failed"]
STEP_OUTCOMES: tuple[StepOutcome, ...] = ("executed", "deferred", "expired_ran", "failed")
"""The closed outcome vocabulary (FR12). ``complete`` is not one of them: an
aggregate event may only claim completion for a pass in which every covered step
reports ``executed`` or ``expired_ran``."""

LedgerState = Literal["ok", "degraded"]

_TS_FIELDS = ("last_completed_ts", "deferred_since_ts", "running_since_ts")


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One covered step's deferral streak."""

    step: str
    last_completed_ts: str | None = None
    deferred_since_ts: str | None = None
    running_since_ts: str | None = None
    deferred_count: int = 0


@dataclass(frozen=True, slots=True)
class DeferralDecision:
    """What one step should do this session, and what its streak looks like."""

    step: str
    defer: bool
    expired: bool
    age_hours: float
    deferred_count: int
    ledger_state: LedgerState


def ledger_path(trw_dir: Path) -> Path:
    """Return the ledger file path for *trw_dir*."""
    return trw_dir / "runtime" / "deferral_ledger.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:  # trw-fail-silent-allow: an unparseable timestamp means "no streak recorded", which opens a fresh one rather than forcing a run
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _plausible_ts(value: str | None) -> bool:
    """``True`` when *value* is absent or a parseable, not-implausibly-future timestamp.

    Audit row 3: the old check only verified ``value`` was a string, so an
    unparseable ``deferred_since_ts`` silently reported age ``0.0`` forever
    and a year-2999 value was "younger than the bound" until that year
    arrives. Either violation must degrade the whole entry, never pass as a
    healthy streak.
    """
    if value is None:
        return True
    parsed = _parse_ts(value)
    if parsed is None:
        return False
    return (parsed - _now()).total_seconds() <= _MAX_FUTURE_SKEW_SECONDS


def _coerce_entry(step: str, raw: object) -> LedgerEntry | None:
    """Coerce one on-disk record, or ``None`` when its shape cannot be trusted."""
    if not isinstance(raw, dict):
        return None
    values: dict[str, str | None] = {}
    for field in _TS_FIELDS:
        candidate = raw.get(field)
        if candidate is not None and not isinstance(candidate, str):
            return None
        if not _plausible_ts(candidate):
            return None
        values[field] = candidate
    count = raw.get("deferred_count", 0)
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return None
    return LedgerEntry(
        step=step,
        last_completed_ts=values["last_completed_ts"],
        deferred_since_ts=values["deferred_since_ts"],
        running_since_ts=values["running_since_ts"],
        deferred_count=count,
    )


def _latch_degraded(path: Path) -> None:
    with _degraded_lock:
        _degraded_since.setdefault(path, time.monotonic())


def _is_latched_degraded(path: Path) -> bool:
    """Was *path* observed degraded within this session-start pass?

    Audit row 2: a corrupt file that gets REPAIRED by the first covered
    step's ``record_completion`` mid-pass must not read as healthy for the
    remaining covered steps of the same pass — that "cold-defers" the rest
    instead of running them, which is exactly the herd/starvation the
    degraded-read contract exists to prevent. The latch expires on its own
    after ``_DEGRADED_LATCH_SECONDS`` so the NEXT session start re-measures
    the real, possibly-healed, file.
    """
    with _degraded_lock:
        started = _degraded_since.get(path)
        if started is None:
            return False
        if time.monotonic() - started > _DEGRADED_LATCH_SECONDS:
            del _degraded_since[path]
            return False
        return True


def read_ledger(trw_dir: Path) -> tuple[dict[str, LedgerEntry], LedgerState]:
    """Read the ledger. Never raises; returns the entries and how trustworthy they are.

    Always performs the real read/parse below — the degraded LATCH never
    short-circuits it, so a genuinely still-corrupt file logs
    ``deferral_ledger_degraded`` on every call, including one a caller is
    actively watching with ``capture_logs()``. The latch only OVERRIDES an
    otherwise-clean result back to ``degraded`` (silently — nothing new
    failed) when a sibling covered step's ``record_completion`` repaired the
    file mid-pass; see :func:`_is_latched_degraded`.
    """
    path = ledger_path(trw_dir)
    latched = _is_latched_degraded(path)
    if not path.exists():
        # Cold start, not a failure: nothing has ever deferred here — UNLESS
        # a sibling step's completion just replaced a corrupt file with one
        # containing only its own entry and this step still isn't in it;
        # that is still "healed mid-pass", not a fresh cold start.
        return ({}, "degraded") if latched else ({}, "ok")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # justified: NFR02 fail-open — unreadable/torn/undecodable is a STATE
        logger.warning("deferral_ledger_degraded", path=str(path), cause="unreadable", exc_info=True)
        _latch_degraded(path)
        return {}, "degraded"
    if not isinstance(raw, dict):
        logger.warning("deferral_ledger_degraded", path=str(path), cause="wrong_shape")
        _latch_degraded(path)
        return {}, "degraded"
    entries: dict[str, LedgerEntry] = {}
    for step, record in raw.items():
        if not isinstance(step, str) or step not in COVERED_STEPS:
            logger.warning("deferral_ledger_degraded", path=str(path), cause="unknown_step")
            _latch_degraded(path)
            return {}, "degraded"
        coerced = _coerce_entry(step, record)
        if coerced is None:
            logger.warning("deferral_ledger_degraded", path=str(path), cause="bad_entry", step=step)
            _latch_degraded(path)
            return {}, "degraded"
        entries[step] = coerced
    # Audit row 2: this read is CLEAN, but if a sibling step's
    # ``record_completion`` repaired the file mid-pass, the rest of this
    # pass must still see degraded — silently, since nothing new failed. The
    # entries themselves are also withheld for consistency with every other
    # degraded return: a degraded state never carries data a caller might
    # trust as a real streak.
    return ({}, "degraded") if latched else (entries, "ok")


def _write_ledger(trw_dir: Path, entries: dict[str, LedgerEntry]) -> bool:
    """Replace the whole file atomically. Returns False when the write failed.

    A step key is overwritten in place, never appended: the whole map is read,
    the one key merged, and the file replaced via a temporary sibling plus
    ``os.replace`` so a concurrent reader always sees one full state or the
    other (NFR04).
    """
    path = ledger_path(trw_dir)
    serialisable = {
        step: {
            "last_completed_ts": entry.last_completed_ts,
            "deferred_since_ts": entry.deferred_since_ts,
            "running_since_ts": entry.running_since_ts,
            "deferred_count": entry.deferred_count,
        }
        for step, entry in sorted(entries.items())
        if step in COVERED_STEPS
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".deferral_ledger.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(serialisable, handle, indent=2, sort_keys=True)
            os.replace(tmp_name, path)
        except OSError:
            try:
                os.unlink(tmp_name)
            except (
                OSError
            ):  # trw-fail-silent-allow: best-effort temp cleanup; the real failure is re-raised on the next line
                pass
            raise
    except OSError:
        logger.warning("deferral_ledger_degraded", path=str(path), cause="unwritable", exc_info=True)
        # trw-fail-silent-allow: NFR02 — False IS the degraded state the caller reports; the step still runs or defers
        return False
    return True


def _merge(trw_dir: Path, step: str, entry: LedgerEntry) -> LedgerState:
    """Read-merge-write one step key. Returns the resulting trust state.

    NFR02: a write failure never blocks the step it describes and never raises.
    The broad guard is deliberate — the caller has already taken its decision,
    and an unexpected writer failure must degrade to a reported STATE, never to
    an exception escaping into ``trw_session_start``.
    """
    entries, state = read_ledger(trw_dir)
    entries[step] = entry
    try:
        written = _write_ledger(trw_dir, entries)
    except Exception:  # justified: NFR02 fail-open — a failed write is a state, not an error
        logger.warning("deferral_ledger_degraded", step=step, cause="write_raised", exc_info=True)
        return "degraded"
    return state if written else "degraded"


def record_deferral(trw_dir: Path, step: str, *, entry: LedgerEntry | None, now: datetime) -> LedgerState:
    """Open or extend *step*'s deferral streak."""
    base = entry or LedgerEntry(step=step)
    since = base.deferred_since_ts or now.isoformat()
    return _merge(
        trw_dir,
        step,
        replace(base, step=step, deferred_since_ts=since, deferred_count=base.deferred_count + 1),
    )


def record_completion(trw_dir: Path, step: str) -> LedgerState:
    """Record that *step* ran, clearing its streak and its forced-run claim."""
    from trw_mcp.state._deferral_claims import release_claim

    release_claim(trw_dir, step)
    entries, _state = read_ledger(trw_dir)
    base = entries.get(step) or LedgerEntry(step=step)
    return _merge(
        trw_dir,
        step,
        replace(
            base,
            step=step,
            last_completed_ts=_now().isoformat(),
            deferred_since_ts=None,
            running_since_ts=None,
            deferred_count=0,
        ),
    )


def claim_forced_run(trw_dir: Path, step: str, *, max_deferral_hours: int) -> bool:
    """Try to become the single process that runs an expired *step*.

    Thin re-export: the actual arbitration is a per-step lease file, split
    into :mod:`trw_mcp.state._deferral_claims` to keep this module under the
    350 effective-LOC gate. Imported lazily (not at module top level) because
    that sibling imports ``read_ledger``/``_write_ledger``/``LedgerEntry``
    back from here.
    """
    from trw_mcp.state._deferral_claims import claim_forced_run as _claim_forced_run

    return _claim_forced_run(trw_dir, step, max_deferral_hours=max_deferral_hours)


def step_deferral_decision(
    trw_dir: Path,
    step: str,
    *,
    under_pressure: bool,
    max_deferral_hours: int,
) -> DeferralDecision:
    """Decide whether *step* defers this session, and record the streak.

    Expiry is inclusive: the step runs once ``age_hours >= max_deferral_hours``.
    The first deferral of a streak sets ``deferred_since_ts`` to now, so its age
    is ``0.0`` and expiry cannot fire on a first deferral.
    """
    entries, state = read_ledger(trw_dir)
    entry = entries.get(step)
    if not under_pressure:
        return DeferralDecision(
            step=step,
            defer=False,
            expired=False,
            age_hours=0.0,
            deferred_count=entry.deferred_count if entry else 0,
            ledger_state=state,
        )
    if state == "degraded":
        # An EXISTING ledger that cannot be trusted still needs single-winner
        # arbitration (audit row 2): unconditionally returning "run" here,
        # with no claim, is exactly what let every peer process run the same
        # expensive step at once. A loser still defers this round rather than
        # spin — the next session start tries again.
        if claim_forced_run(trw_dir, step, max_deferral_hours=max_deferral_hours):
            return DeferralDecision(
                step=step, defer=False, expired=True, age_hours=0.0, deferred_count=0, ledger_state="degraded"
            )
        return DeferralDecision(
            step=step, defer=True, expired=False, age_hours=0.0, deferred_count=0, ledger_state="degraded"
        )

    now = _now()
    since = _parse_ts(entry.deferred_since_ts) if entry else None
    age_hours = 0.0 if since is None else max((now - since).total_seconds() / 3600.0, 0.0)
    expired = since is not None and age_hours >= max_deferral_hours
    if expired and claim_forced_run(trw_dir, step, max_deferral_hours=max_deferral_hours):
        return DeferralDecision(
            step=step,
            defer=False,
            expired=True,
            age_hours=round(age_hours, 4),
            deferred_count=entry.deferred_count if entry else 0,
            ledger_state=state,
        )
    write_state = record_deferral(trw_dir, step, entry=entry, now=now)
    # Audit row 2: a MISSING-but-unwritable ledger can never durably record
    # deferred_since_ts, so age_hours would stay 0.0 forever and the bound
    # could never fire. Route the forced run through the same single-winner
    # claim used for ordinary expiry, so N processes sharing an unwritable
    # ledger do not all run this step at once.
    if write_state == "degraded" and claim_forced_run(trw_dir, step, max_deferral_hours=max_deferral_hours):
        return DeferralDecision(
            step=step,
            defer=False,
            expired=True,
            age_hours=round(age_hours, 4),
            deferred_count=entry.deferred_count if entry else 0,
            ledger_state="degraded",
        )
    return DeferralDecision(
        step=step,
        defer=True,
        expired=False,
        age_hours=round(age_hours, 4),
        deferred_count=(entry.deferred_count if entry else 0) + 1,
        ledger_state=write_state,
    )


def step_outcome(decision: DeferralDecision, *, failed: bool = False) -> StepOutcome:
    """Map one decision (plus whether the work raised) onto the FR12 vocabulary."""
    if failed:
        return "failed"
    if decision.expired:
        return "expired_ran"
    if decision.defer:
        return "deferred"
    return "executed"


def deferred_steps_summary(trw_dir: Path) -> tuple[dict[str, dict[str, float | int]], LedgerState]:
    """Project the ledger for the ``writer_pressure`` status block (FR05).

    Read-only: reporting status must never open or extend a streak.
    """
    entries, state = read_ledger(trw_dir)
    now = _now()
    summary: dict[str, dict[str, float | int]] = {}
    for step, entry in sorted(entries.items()):
        since = _parse_ts(entry.deferred_since_ts)
        if since is None:
            continue
        summary[step] = {
            "age_hours": round(max((now - since).total_seconds() / 3600.0, 0.0), 2),
            "deferred_count": entry.deferred_count,
        }
    return summary, state


__all__ = [
    "COVERED_STEPS",
    "STEP_OUTCOMES",
    "DeferralDecision",
    "LedgerEntry",
    "LedgerState",
    "StepOutcome",
    "claim_forced_run",
    "deferred_steps_summary",
    "ledger_path",
    "read_ledger",
    "record_completion",
    "record_deferral",
    "step_deferral_decision",
    "step_outcome",
]
