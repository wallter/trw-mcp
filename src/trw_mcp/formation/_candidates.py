"""Expiring candidate registry for order-independent bootstrap (PRD-CORE-274-FR18).

Belongs to the ``trw_mcp.formation`` facade; re-exported there.

A client with a pinned run ANNOUNCES itself before, after or interleaved with the
formation's creation. The record grants nothing: no membership, no enrollment,
no mailbox. It exists so the orchestrator can ADMIT a named candidate to a slot,
which is the authentication (a candidate never selects its own slot).

Everything identifying lives server-side and is never returned: the pin, the run
path, the worktree path. A caller receives only the random ``candidate_id``, and a
copied id is useless, because pick-up re-checks that the presenting caller holds
the recorded pin AND run.

The registry is capped per coordination root and refuses when full, never
evicting a live record: eviction would let a flood of announcements silently
displace a candidate the orchestrator is about to admit.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from trw_mcp.formation._manifest import FormationError
from trw_mcp.formation._store import _exclusive, read_json_store, write_json_store

CANDIDATE_CAP = 64
_REGISTRY_RELATIVE = ("runtime", "comms-candidates.json")


class CandidateState(str, Enum):
    """``active`` may be admitted; ``admitted`` is named by exactly one slot; ``joining``
    has passed pick-up stage 1. The rest are final (ledger RC-006)."""

    ACTIVE = "active"
    ADMITTED = "admitted"
    JOINING = "joining"
    WITHDRAWN = "withdrawn"
    REVOKED = "revoked"
    PICKED_UP = "picked_up"


LIVE_STATES = frozenset({CandidateState.ACTIVE.value, CandidateState.ADMITTED.value, CandidateState.JOINING.value})
#: Every legal move. Re-entering the same state is legal only as a no-op retry (see set_state).
_TRANSITIONS: dict[str, frozenset[str]] = {
    CandidateState.ACTIVE.value: frozenset(
        {CandidateState.ADMITTED.value, CandidateState.WITHDRAWN.value, CandidateState.REVOKED.value}
    ),
    CandidateState.ADMITTED.value: frozenset(
        {
            CandidateState.ACTIVE.value,  # released by the orchestrator before pick-up
            CandidateState.JOINING.value,
            CandidateState.WITHDRAWN.value,
            CandidateState.REVOKED.value,
        }
    ),
    CandidateState.JOINING.value: frozenset(
        {CandidateState.PICKED_UP.value, CandidateState.WITHDRAWN.value, CandidateState.REVOKED.value}
    ),
    CandidateState.WITHDRAWN.value: frozenset(),
    CandidateState.REVOKED.value: frozenset(),
    CandidateState.PICKED_UP.value: frozenset(),
}


class CandidateError(FormationError):
    """A closed-reason refusal: ``reason`` is safe to return to the caller."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    pin_key: str
    run_path: str
    worktree: str | None
    client: str
    announced_at: float
    expires_at: float
    state: str
    #: The formation whose slot admitted this candidate, set with the ``admitted`` state.
    admitted_formation: str | None = None

    def live(self, now: float) -> bool:
        return self.state in LIVE_STATES and self.expires_at > now


def _path(trw_dir: Path) -> Path:
    return trw_dir.joinpath(*_REGISTRY_RELATIVE)


def _read(trw_dir: Path) -> dict[str, Candidate]:
    path = _path(trw_dir)
    raw = read_json_store(path, section="candidates", label="candidate registry")
    try:
        return {key: Candidate(**value) for key, value in raw.items()}
    except TypeError as exc:  # a record whose FIELDS do not match this build's Candidate
        raise FormationError(f"candidate registry {path} is unreadable: {exc}") from exc


def _write(trw_dir: Path, records: dict[str, Candidate]) -> None:
    write_json_store(_path(trw_dir), section="candidates", records={k: asdict(v) for k, v in records.items()})


def _same(candidate: Candidate, pin_key: str, run_path: Path) -> bool:
    return candidate.pin_key == pin_key and Path(candidate.run_path).resolve() == run_path.resolve()


def announce(
    trw_dir: Path,
    *,
    pin_key: str,
    run_path: Path,
    worktree: Path | None,
    client: str,
    ttl_seconds: float,
    now: float | None = None,
) -> Candidate:
    """Register (or refresh) the caller's candidate; the same pin and run keep one handle."""
    moment = time.time() if now is None else now
    path = _path(trw_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive(path):
        # Past-deadline and final records are dropped: they are not live, so
        # removing them is housekeeping, never eviction.
        records = {k: v for k, v in _read(trw_dir).items() if v.live(moment)}
        mine = next((c for c in records.values() if _same(c, pin_key, run_path)), None)
        if mine is None and len(records) >= CANDIDATE_CAP:
            raise CandidateError("candidate_registry_full")
        candidate = Candidate(
            candidate_id=mine.candidate_id if mine else secrets.token_hex(16),
            pin_key=pin_key,
            run_path=str(run_path.resolve()),
            worktree=str(worktree) if worktree is not None else None,
            client=client,
            announced_at=mine.announced_at if mine else moment,
            expires_at=moment + ttl_seconds,
            state=mine.state if mine else CandidateState.ACTIVE.value,
            admitted_formation=mine.admitted_formation if mine else None,
        )
        records[candidate.candidate_id] = candidate
        _write(trw_dir, records)
    return candidate


_KEEP = object()


def _apply_transition(found: Candidate, state: CandidateState | str, admitted_formation: object) -> Candidate:
    """Validate and apply one transition to *found*; the shared core of :func:`set_state`
    and :func:`set_states` (PRD-FIX-149 review R2 -- one validation, not two copies).

    An illegal transition (for example reviving a revoked candidate) raises
    FormationError rather than silently rewriting history. A retry is idempotent
    only when it changes nothing: re-admitting a candidate that another formation
    already admitted is refused here, which is what makes two orchestrators
    racing for one handle end with exactly one winner (PRD-FIX-149 FR04/FR05).
    """
    state = CandidateState(state).value
    retry = state == found.state and admitted_formation in (_KEEP, found.admitted_formation)
    if not retry and state not in _TRANSITIONS.get(found.state, frozenset()):
        held = f" (admitted by {found.admitted_formation!r})" if found.admitted_formation else ""
        raise FormationError(f"candidate {found.candidate_id!r} cannot move from {found.state}{held} to {state}")
    changes: dict[str, Any] = {"state": state}
    if admitted_formation is not _KEEP:
        changes["admitted_formation"] = admitted_formation
    return Candidate(**{**asdict(found), **changes})


def set_state(
    trw_dir: Path, candidate_id: str, state: CandidateState | str, *, admitted_formation: object = _KEEP
) -> Candidate | None:
    """Move one candidate to *state* (and optionally its admitting formation); None when unknown.

    See :func:`_apply_transition` for the transition-legality contract. Held
    under the registry lock for the single candidate, read-modify-write.
    """
    path = _path(trw_dir)
    if not path.is_file():
        return None
    with _exclusive(path):
        records = _read(trw_dir)
        found = records.get(candidate_id)
        if found is None:
            return None
        records[candidate_id] = _apply_transition(found, state, admitted_formation)
        _write(trw_dir, records)
        return records[candidate_id]


@dataclass(frozen=True)
class StateTransition:
    """One requested move, batched with others under :func:`set_states`."""

    candidate_id: str
    state: CandidateState | str
    admitted_formation: object = _KEEP
    #: When not ``_KEEP``, the candidate's CURRENT ``admitted_formation`` must
    #: equal this value while the candidate is actually ``admitted``, or the
    #: whole batch refuses -- an ownership CAS for a release: only the
    #: formation that holds a candidate may free it back to the pool
    #: (PRD-FIX-149 review R2). A candidate that is no longer ``admitted``
    #: (already released, expired, withdrawn) has nothing to own, so this
    #: check does not apply to it.
    expected_admitted_formation: object = _KEEP
    #: When False, a missing candidate or a transition that no longer applies
    #: is silently skipped rather than refusing the whole batch -- for a
    #: best-effort release of a candidate that may already be gone. An
    #: ownership CAS violation (see above) is never silently skipped,
    #: regardless of this flag.
    required: bool = True


def set_states(trw_dir: Path, transitions: list[StateTransition]) -> list[Candidate]:
    """Apply every transition under ONE hold of the registry lock, or apply none.

    ``commit_admissions`` used to call :func:`set_state` once PER candidate,
    each call taking and releasing the lock separately -- so a multi-candidate
    admission whose SECOND candidate's transition was illegal (already claimed
    by a concurrent admitter, say) left the FIRST candidate already ADMITTED,
    with no manifest ever committed to name it (PRD-FIX-149 review R2). Reading
    every candidate once, validating every requested move against that single
    snapshot, and calling the store's ``_write`` exactly ONCE -- only after
    every validation in the batch passed -- makes a partial application
    impossible: nothing is persisted until the whole batch is legal.
    """
    if not transitions:
        return []
    path = _path(trw_dir)
    if not path.is_file():
        if any(op.required for op in transitions):
            raise FormationError(f"candidate registry {path} does not exist; nothing to transition")
        return []
    with _exclusive(path):
        records = _read(trw_dir)
        revised: list[Candidate] = []
        for op in transitions:
            found = records.get(op.candidate_id)
            if found is None:
                if op.required:
                    raise FormationError(f"unknown candidate {op.candidate_id!r}")
                continue
            if (
                op.expected_admitted_formation is not _KEEP
                and found.state == CandidateState.ADMITTED.value
                and found.admitted_formation != op.expected_admitted_formation
            ):
                # Ownership is never optional, whether or not this op is
                # best-effort: a caller that is not the recorded admitter must
                # never move this candidate out from under its real owner.
                raise FormationError(
                    f"candidate {op.candidate_id!r} is admitted by {found.admitted_formation!r}, not "
                    f"{op.expected_admitted_formation!r}; refusing a transition from a non-owning formation"
                )
            try:
                updated = _apply_transition(found, op.state, op.admitted_formation)
            except FormationError:
                if op.required:
                    raise
                continue
            records[op.candidate_id] = updated
            revised.append(updated)
        if revised:
            _write(trw_dir, records)
        return revised


def candidate(trw_dir: Path, candidate_id: str) -> Candidate | None:
    return _read(trw_dir).get(candidate_id)


def candidate_for(trw_dir: Path, pin_key: str, run_path: Path) -> Candidate | None:
    """The caller's own record, whatever its state (the newest when several exist)."""
    mine = [c for c in _read(trw_dir).values() if _same(c, pin_key, run_path)]
    return max(mine, key=lambda c: c.announced_at) if mine else None


def live_candidates(trw_dir: Path, now: float | None = None) -> list[Candidate]:
    moment = time.time() if now is None else now
    return sorted((c for c in _read(trw_dir).values() if c.live(moment)), key=lambda c: c.announced_at)


def worktree_admitted_by(trw_dir: Path, worktree: Path, formation_id: str) -> bool:
    """Whether *formation_id* currently holds an ``admitted`` candidate for *worktree*.

    Used by the FR17 coordination store (PRD-FIX-149 review R6) to tell a
    genuinely-held worktree apart from a STALE membership record naming a
    formation that released, lost, or never actually held the candidate: only
    the former refuses a new admission. Ownership does not expire merely
    because the candidate's own announcement TTL lapsed, so this checks state
    and admitting formation only, never liveness.
    """
    key = str(worktree.resolve())
    return any(
        c.worktree == key and c.state == CandidateState.ADMITTED.value and c.admitted_formation == formation_id
        for c in _read(trw_dir).values()
    )


__all__ = [
    "CANDIDATE_CAP",
    "Candidate",
    "CandidateError",
    "CandidateState",
    "StateTransition",
    "announce",
    "candidate",
    "candidate_for",
    "live_candidates",
    "set_state",
    "set_states",
    "worktree_admitted_by",
]
