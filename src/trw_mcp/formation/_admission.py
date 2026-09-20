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

from trw_mcp.formation._candidates import Candidate, CandidateState, candidate, set_state
from trw_mcp.formation._coordination import record_worktree_member
from trw_mcp.formation._manifest import AdmissionRefused, FormationError, FormationManifest, FormationMember


def admitted_members(
    trw_dir: Path,
    before: FormationManifest | None,
    members: list[FormationMember],
    *,
    revision: int,
    supplied: dict[str, dict[str, Any]],
) -> tuple[list[FormationMember], list[tuple[FormationMember, Candidate]], list[str]]:
    """Stamp admitting revisions; return the new admissions and the handles this revision releases.

    *supplied* is the raw per-member payload, checked for a forged revision. A
    candidate is admissible only while ``active``: admission moves it to
    ``admitted`` (see :func:`commit_admissions`), so a second orchestrator at the
    same root cannot admit the same handle to another slot (lane C review, D2).
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
        if found is None or not found.live(now) or found.state != CandidateState.ACTIVE:
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
    """Write the side records of an admission BEFORE the manifest that names it commits.

    Called under the manifest lock, ahead of the manifest write: if a worktree
    record or a candidate transition fails, the admission is not committed, so an
    admission can never be durable without the FR17 record its member needs
    (lane C review, M2). A record written for an admission whose manifest write
    then fails grants nothing: binding still needs the admitted join.
    """
    # Every fallible record write first; candidate transitions only once they all
    # succeeded, so a failed admission leaves each candidate exactly as it was.
    for member, found in admitted:
        if found.worktree is not None:
            record_worktree_member(
                trw_dir,
                Path(found.worktree),
                formation_id=formation_id,
                member_id=member.member_id,
                manifest_revision=int(member.admitted_revision or 0),
                creating_run=orchestrator_run,
            )
    for handle in released:
        found_released = candidate(trw_dir, handle)
        if found_released is not None and found_released.state == CandidateState.ADMITTED:
            set_state(trw_dir, handle, CandidateState.ACTIVE, admitted_formation=None)
    for _member, found in admitted:
        # The admitting formation is indexed on the candidate, so pick-up reads ONE
        # manifest instead of scanning every registered formation (C review, S1).
        set_state(trw_dir, found.candidate_id, CandidateState.ADMITTED, admitted_formation=formation_id)


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
    from trw_mcp.state.persistence import FileStateReader, FileStateWriter

    run_yaml = run_path / "meta" / "run.yaml"
    if not run_yaml.is_file():
        return False
    data = FileStateReader().read_yaml(run_yaml)
    if "formation_id" not in data:
        return False
    data["revoked_formation_id"] = data.pop("formation_id")
    data["revoked_member_id"] = data.pop("member_id", None)
    FileStateWriter().write_yaml(run_yaml, data)
    return True


__all__ = ["admitted_members", "commit_admissions", "require_join_admitted", "revoke_run_stamp"]
