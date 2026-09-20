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

import json
import secrets
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from trw_mcp.formation._manifest import FormationError
from trw_mcp.formation._store import _exclusive, write_owner_only

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
#: Every legal move. Re-entering the same state is always legal (retries are idempotent).
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
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): Candidate(**v) for k, v in raw["candidates"].items()}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise FormationError(f"candidate registry {path} is unreadable") from exc


def _write(trw_dir: Path, records: dict[str, Candidate]) -> None:
    path = _path(trw_dir)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload: dict[str, Any] = {"candidates": {k: asdict(v) for k, v in records.items()}}
    write_owner_only(tmp, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode())
    tmp.replace(path)


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


def set_state(
    trw_dir: Path, candidate_id: str, state: CandidateState | str, *, admitted_formation: object = _KEEP
) -> Candidate | None:
    """Move one candidate to *state* (and optionally its admitting formation); None when unknown.

    An illegal transition (for example reviving a revoked candidate) raises
    FormationError rather than silently rewriting history.
    """
    state = CandidateState(state).value
    path = _path(trw_dir)
    if not path.is_file():
        return None
    with _exclusive(path):
        records = _read(trw_dir)
        found = records.get(candidate_id)
        if found is None:
            return None
        if state != found.state and state not in _TRANSITIONS.get(found.state, frozenset()):
            raise FormationError(f"candidate {candidate_id!r} cannot move from {found.state} to {state}")
        changes: dict[str, Any] = {"state": state}
        if admitted_formation is not _KEEP:
            changes["admitted_formation"] = admitted_formation
        records[candidate_id] = Candidate(**{**asdict(found), **changes})
        _write(trw_dir, records)
        return records[candidate_id]


def candidate(trw_dir: Path, candidate_id: str) -> Candidate | None:
    return _read(trw_dir).get(candidate_id)


def candidate_for(trw_dir: Path, pin_key: str, run_path: Path) -> Candidate | None:
    """The caller's own record, whatever its state (the newest when several exist)."""
    mine = [c for c in _read(trw_dir).values() if _same(c, pin_key, run_path)]
    return max(mine, key=lambda c: c.announced_at) if mine else None


def live_candidates(trw_dir: Path, now: float | None = None) -> list[Candidate]:
    moment = time.time() if now is None else now
    return sorted((c for c in _read(trw_dir).values() if c.live(moment)), key=lambda c: c.announced_at)


__all__ = [
    "CANDIDATE_CAP",
    "Candidate",
    "CandidateError",
    "CandidateState",
    "announce",
    "candidate",
    "candidate_for",
    "live_candidates",
    "set_state",
]
