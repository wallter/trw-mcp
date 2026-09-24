"""Candidate admission and the strict join gate (PRD-CORE-274-FR18).

Belongs to the ``trw_mcp.formation`` facade; used by ``_join``.

THE ORCHESTRATOR'S EXPLICIT ACT IS THE AUTHENTICATION. A slot is filled either
first-come, only when the orchestrator declared it ``open_join: true``, or by the
one candidate the orchestrator named in ``admitted_candidate`` through creation or
revise. The admitting revision is recorded server-side, never taken from the
payload, so a revise cannot backdate an admission.

A worktree candidate's admission also writes the FR17 worktree membership record,
because that record is what lets the member's authority-bearing calls reach the
main root at all.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from trw_mcp.formation._candidates import Candidate, CandidateState, StateTransition, candidate, set_states
from trw_mcp.formation._coordination import record_worktree_member, release_worktree_member
from trw_mcp.formation._manifest import AdmissionRefused, FormationError, FormationManifest, FormationMember


def admitted_members(
    trw_dir: Path,
    before: FormationManifest | None,
    members: list[FormationMember],
    *,
    formation_id: str,
    revision: int,
    supplied: dict[str, dict[str, Any]],
) -> tuple[list[FormationMember], list[tuple[FormationMember, Candidate]], list[str]]:
    """Stamp admitting revisions; return the new admissions and the handles this revision releases.

    *supplied* is the raw per-member payload, checked for a forged revision. A
    candidate is admissible while ``active``, or while ``admitted`` by THIS SAME
    *formation_id* and not yet named by ANY member of *before* (PRD-FIX-149
    review R9): a crash between the candidate's CAS and the manifest write can
    strand a candidate ``admitted`` by a formation whose own on-disk manifest
    never named it anywhere, and a retry by that same formation must be able to
    finish what it started rather than being told the candidate is unavailable
    forever. The "not yet named by *before*" half of that check is what keeps
    D2 intact: a candidate this formation already admitted to a DIFFERENT,
    already-committed member is still refused for a second slot -- only a
    candidate no committed manifest anywhere references gets the crash-retry
    exception. A DIFFERENT formation still cannot admit the same handle at all
    (lane C review, D2).
    """
    previous = {m.member_id: m for m in before.members} if before is not None else {}
    stamped: list[FormationMember] = []
    admitted: list[tuple[FormationMember, Candidate]] = []
    now = time.time()
    kept = {m.admitted_candidate for m in members if m.admitted_candidate is not None}
    released = [
        m.admitted_candidate
        for m in previous.values()
        if m.admitted_candidate is not None and m.admitted_candidate not in kept and m.run_path is None
    ]
    committed_handles = {m.admitted_candidate for m in previous.values() if m.admitted_candidate is not None}
    for member in members:
        if "admitted_revision" in supplied.get(member.member_id, {}):
            raise FormationError(f"admitted_revision is server-set; member {member.member_id!r} supplied one")
        prior = previous.get(member.member_id)
        handle = member.admitted_candidate
        if handle is None:
            stamped.append(member.model_copy(update={"admitted_revision": None}))
            continue
        if prior is not None and prior.admitted_candidate == handle:
            stamped.append(member.model_copy(update={"admitted_revision": prior.admitted_revision}))
            continue
        if member.run_path is not None:
            raise FormationError(f"member {member.member_id!r} is already joined; admit a candidate to a pending slot")
        found = candidate(trw_dir, handle)
        stranded_by_self = (
            found is not None
            and found.state == CandidateState.ADMITTED.value
            and found.admitted_formation == formation_id
            and handle not in committed_handles
        )
        if found is None or not found.live(now) or not (found.state == CandidateState.ACTIVE.value or stranded_by_self):
            raise FormationError(
                f"candidate_not_admissible: {handle!r} is not a live, unadmitted candidate at this coordination root"
            )
        admitted_member = member.model_copy(update={"admitted_revision": revision})
        stamped.append(admitted_member)
        admitted.append((admitted_member, found))
    return stamped, admitted, released


def commit_admissions(
    trw_dir: Path,
    formation_id: str,
    orchestrator_run: Path,
    admitted: list[tuple[FormationMember, Candidate]],
    released: list[str],
) -> None:
    """Every candidate transition in this call lands together, or none does; worktree
    records are written only once every transition has committed.

    PRD-FIX-149 review R2: the previous ordering wrote worktree records BEFORE
    the candidate transitions, and called ``set_state`` once PER candidate --
    so a failure on the SECOND candidate of a multi-candidate admission left
    the FIRST already ADMITTED (and its worktree record already written), with
    no manifest ever committed to name it. ``set_states`` takes the registry
    lock ONCE and validates every requested move -- admissions AND releases
    together -- before writing any of them (the ACTIVE check in
    ``admitted_members`` still runs unlocked; this call is what makes the
    actual state mutation atomic and race-safe, PRD-FIX-149 FR04). Release
    (ADMITTED -> ACTIVE) is a compare-and-swap on ``admitted_formation``: only
    the formation that holds a candidate may free it (FR05's sibling rule for
    the release direction).

    Worktree records are written LAST, after every transition in this call
    committed. A candidate transition is reversible (this call simply never
    applies it, so nothing is stranded); a worktree membership record is not
    costlessly undone once written. If a worktree record fails after the
    transitions committed, the newly-admitted candidates are rolled back to
    ACTIVE (a compensating batch) before the error propagates, so a worktree
    store failure cannot strand a candidate ADMITTED with nothing -- no
    manifest, no record -- naming it.

    PRD-FIX-149 review R6/R7: a released candidate's worktree record is deleted
    right here, once its release CAS has committed -- otherwise nothing ever
    removes it, and a worktree that formation F1 once admitted could never be
    admitted by any OTHER formation again (``record_worktree_member`` refuses
    to replace a record naming a different formation). And when the record
    WRITE fails mid-batch, every record this call already wrote earlier in the
    SAME batch is deleted along with the candidate rollback, so a two-candidate
    admission whose second record fails does not leave the first one's stale
    record behind; if that compensating delete or rollback itself raises, it is
    chained onto the original failure with ``raise ... from`` rather than
    replacing it, so the write failure that actually caused this is never lost.
    """
    released_worktrees = [
        Path(found.worktree)
        for handle in released
        if (found := candidate(trw_dir, handle)) is not None and found.worktree is not None
    ]
    admission_ops = [
        StateTransition(found.candidate_id, CandidateState.ADMITTED, admitted_formation=formation_id)
        for _member, found in admitted
    ]
    release_ops = [
        StateTransition(
            handle,
            CandidateState.ACTIVE,
            admitted_formation=None,
            expected_admitted_formation=formation_id,
            required=False,
        )
        for handle in released
    ]
    if admission_ops or release_ops:
        set_states(trw_dir, admission_ops + release_ops)
    for worktree in released_worktrees:
        # Best-effort CAS delete: a record this formation no longer owns (already
        # reassigned, or never matched) is left untouched by release_worktree_member.
        release_worktree_member(trw_dir, worktree, formation_id=formation_id)
    written: list[Path] = []
    try:
        for member, found in admitted:
            if found.worktree is not None:
                worktree = Path(found.worktree)
                record_worktree_member(
                    trw_dir,
                    worktree,
                    formation_id=formation_id,
                    member_id=member.member_id,
                    manifest_revision=int(member.admitted_revision or 0),
                    creating_run=orchestrator_run,
                )
                written.append(worktree)
    except Exception as exc:
        for worktree in written:
            release_worktree_member(trw_dir, worktree, formation_id=formation_id)
        if admission_ops:
            rollback = [
                StateTransition(
                    op.candidate_id,
                    CandidateState.ACTIVE,
                    admitted_formation=None,
                    expected_admitted_formation=formation_id,
                    required=False,
                )
                for op in admission_ops
            ]
            try:
                set_states(trw_dir, rollback)
            except Exception as rollback_exc:
                raise rollback_exc from exc
        raise


def require_join_admitted(
    trw_dir: Path,
    member: FormationMember,
    *,
    candidate_id: str | None,
    run_path: Path,
    pin_key: str | None,
) -> None:
    """A pending slot joins only if it is open, or its admitted candidate IS this caller."""
    if member.open_join:
        return
    if member.admitted_candidate is None or candidate_id != member.admitted_candidate:
        raise AdmissionRefused(
            f"join_not_admitted: member {member.member_id!r} is not open_join and did not admit this caller; "
            "the orchestrator must admit an announced candidate or declare the slot open_join"
        )
    found = candidate(trw_dir, candidate_id)
    if (
        found is None
        or not found.live(time.time())
        or found.pin_key != pin_key
        or Path(found.run_path).resolve() != run_path.resolve()
    ):
        raise AdmissionRefused(f"admission_revoked: candidate for member {member.member_id!r} no longer matches")


def revoke_run_stamp(run_path: Path) -> bool:
    """FR18 compensation: mark a pick-up's run stamp revoked. True when a stamp was revoked.

    The ids move to ``revoked_formation_id``/``revoked_member_id``, so the run no
    longer resolves any formation (``stamped_ids`` reads only the live keys) and
    therefore cannot bind, while the evidence of what it had joined stays on disk.
    Manifest fields are deliberately untouched: they are the orchestrator's to revise.
    """
    from trw_mcp.state._run_yaml_update import update_run_yaml

    revoked: list[bool] = []

    def _revoke(data: dict[str, object]) -> None:
        if "formation_id" in data:
            data["revoked_formation_id"] = data.pop("formation_id")
            data["revoked_member_id"] = data.pop("member_id", None)
            revoked.append(True)

    update_run_yaml(run_path, _revoke)
    return bool(revoked)


__all__ = ["admitted_members", "commit_admissions", "require_join_admitted", "revoke_run_stamp"]
