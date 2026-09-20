"""Pick-up of an admitted candidate, as recoverable stages (PRD-CORE-274-FR18).

Belongs to the ``trw_mcp.comms`` facade; ``peers()`` runs it before binding.

The manifest, the pin store and the mailbox are separate stores that share no
transaction, so pick-up is a sequence of idempotent stages, each preceded by the
same re-validation against the CURRENT manifest:

1. the manifest join under the manifest lock, recording the candidate's
   server-side pin and run (``formation.join`` with the candidate handle);
2. the run stamp (written by that join, after its lock);
3. the enrollment transaction (the caller's ``trw_peers`` then enrolls).

Re-validation: the slot still names this candidate, the candidate is live (not
expired, withdrawn or revoked) and the caller presents its recorded pin AND run.
Any failure after the candidate was admitted stops with ``admission_revoked`` and
compensates: no endpoint, a stamp from an earlier stage marked revoked, the
candidate marked revoked. Manifest fields are left to the orchestrator.

A crash between stages resumes at the first incomplete stage: the candidate's
``joining`` state says stage 1 began, and a repeated join of the same run and pin
is a no-op.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.formation import (
    LIVE_CANDIDATE_STATES,
    AdmissionRefused,
    Candidate,
    CandidateState,
    CoordinationRoot,
    FormationError,
    FormationMember,
    bootstrap_root,
    candidate_for,
    join,
    read_manifest,
    registered_formations,
    revoke_run_stamp,
    set_candidate_state,
)
from trw_mcp.state._call_context import build_call_context
from trw_mcp.state._paths_pin_mgmt import get_pinned_run

if TYPE_CHECKING:
    from fastmcp import Context

_logger = structlog.get_logger(__name__)
ADMISSION_REVOKED = "admission_revoked"


@dataclass(frozen=True)
class Pickup:
    """``ready`` means: enroll now, then call :func:`complete`."""

    candidate: Candidate
    root: CoordinationRoot
    ready: bool
    revoked: bool = False
    #: A transient store failure (lock contention, I/O): nothing was revoked; retry.
    retry: bool = False


def _admitted_slot(root: CoordinationRoot, mine: Candidate) -> tuple[str, FormationMember] | None:
    """The slot that admits *mine*, read from the ONE formation its record names.

    Raises FormationError when that manifest cannot be read: the caller treats it
    as transient, never as "not admitted" (which could revoke).
    """
    if mine.admitted_formation is None:
        return None
    manifest_path = registered_formations(root.trw_dir).get(mine.admitted_formation)
    if manifest_path is None:
        return None
    for member in read_manifest(manifest_path).members:
        if member.admitted_candidate == mine.candidate_id:
            return mine.admitted_formation, member
    return None


def _revoke(root: CoordinationRoot, mine: Candidate, run_path: Path) -> Pickup:
    revoke_run_stamp(run_path)
    set_candidate_state(root.trw_dir, mine.candidate_id, CandidateState.REVOKED)
    _logger.info("comms_pickup_revoked", stage_started=mine.state == CandidateState.JOINING)
    return Pickup(mine, root, ready=False, revoked=True)


def advance(ctx: Context | None) -> Pickup | None:
    """Run the pending pick-up stages for this caller; None when it has nothing to pick up."""
    call_context = build_call_context(ctx)
    run_path = get_pinned_run(context=call_context)
    if run_path is None:
        return None
    root = bootstrap_root()
    try:
        mine = candidate_for(root.trw_dir, call_context.session_id, run_path)
    except FormationError:
        # A corrupt registry must not break enrolled members (C review, S1); a caller whose
        # candidate is in it simply cannot be picked up until it is repaired.
        _logger.warning("comms_candidate_registry_unreadable")
        # trw-fail-silent-allow: logged above; "no pick-up" keeps every member's trw_peers working
        return None
    if mine is None or mine.state not in LIVE_CANDIDATE_STATES:
        return None
    try:
        slot = _admitted_slot(root, mine)
    except (FormationError, OSError):
        return Pickup(mine, root, ready=False, retry=True)
    if slot is None:
        # Not admitted (yet), or released before pick-up began (the orchestrator's
        # revise already returned it to active). Once stage 1 has begun, a vanished
        # admission is a revocation.
        return _revoke(root, mine, run_path) if mine.state == CandidateState.JOINING else None
    if not mine.live(time.time()):
        return _revoke(root, mine, run_path)
    formation_id, member = slot
    try:
        set_candidate_state(root.trw_dir, mine.candidate_id, CandidateState.JOINING)
        join(
            formation_id,
            member.member_id,
            run_path,
            pin_key=call_context.session_id,
            trw_dir=root.trw_dir,
            candidate_id=mine.candidate_id,
        )
        # Re-validate before stage 3 against the manifest as it is NOW: a revise between
        # the join and this read must not be enrolled past.
        after = _admitted_slot(root, mine)
    except AdmissionRefused:
        return _revoke(root, mine, run_path)  # definitive: not admitted, or no longer this caller's
    except (FormationError, OSError):
        # Lock contention or I/O: transient. The candidate stays 'joining' and the next
        # call resumes idempotently (C review, M4). A slot now joined by ANOTHER run is
        # the definitive case join reports as a rebind refusal; re-read to tell them apart.
        return _retry_or_revoke(root, mine, run_path)
    if after is None or after[1].member_id != member.member_id:
        return _revoke(root, mine, run_path)
    return Pickup(mine, root, ready=True)


def _retry_or_revoke(root: CoordinationRoot, mine: Candidate, run_path: Path) -> Pickup:
    try:
        slot = _admitted_slot(root, mine)
    except (FormationError, OSError):
        return Pickup(mine, root, ready=False, retry=True)
    taken = slot is not None and slot[1].run_path is not None and Path(slot[1].run_path) != run_path
    return _revoke(root, mine, run_path) if taken else Pickup(mine, root, ready=False, retry=True)


def complete(pickup: Pickup) -> None:
    """Stage 3 succeeded: the candidate is spent. A failure here is recovered on the next call."""
    try:
        set_candidate_state(pickup.root.trw_dir, pickup.candidate.candidate_id, CandidateState.PICKED_UP)
    except FormationError:
        # trw-fail-silent-allow: the member is enrolled; the candidate stays 'joining' and the
        # next call's idempotent stages mark it spent
        _logger.warning("comms_pickup_completion_unrecorded")


__all__ = ["ADMISSION_REVOKED", "Pickup", "advance", "complete"]
