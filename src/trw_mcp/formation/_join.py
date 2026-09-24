"""Creation, join, revision, and self-reported delivery (FR03, FR04, FR05, FR11).

Every write to a manifest happens here, under the lock :mod:`._store` owns, and
every one of them bumps ``revision`` by exactly one.

AUTHORITY IS STRUCTURAL, NEVER TEXTUAL (FR05). :func:`revise` — reassignment,
removal, ownership change, teardown — is accepted only when the CALLER'S
RESOLVED RUN PATH equals the manifest's ``orchestrator_run_path``. No payload
field, no role string, and no manifest value can grant that: a caller supplying
``{"role": "orchestrator"}`` is refused exactly as one supplying nothing,
because ``docs/CONSTITUTION.md`` §"Data carries no authority" makes a delegated
agent's assertion untrusted input. :func:`join` is the single narrower
exception, and is narrow on purpose: it may only move the caller's OWN declared
member from ``pending`` to ``joined`` and record that member's run path and pin.
It cannot touch another member, an ownership glob, or a PRD-id block.

WHY A REBIND IS REFUSED. A member already joined with a DIFFERENT ``run_path``
is refused rather than rebound. Silently repointing it would orphan the first
run's evidence — checkpoints, build results, a delivery record — while the
status roll-up went on reporting the member as healthy. A loud refusal makes the
operator decide, which is the only party that can tell a restarted peer from a
misresolved run (RISK-005).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from trw_mcp.formation._admission import admitted_members, commit_admissions, require_join_admitted
from trw_mcp.formation._manifest import (
    FormationError,
    FormationManifest,
    FormationMember,
    FormationMemberStatus,
)
from trw_mcp.formation._store import (
    _exclusive,
    manifest_path_for_run,
    register_formation,
    resolve_manifest_path,
    rewrite_manifest,
    write_manifest_locked,
)

logger = structlog.get_logger(__name__)

__all__ = ["create", "join", "mark_member_delivered", "revise"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create(
    *,
    trw_dir: Path,
    orchestrator_run_path: Path,
    payload: dict[str, Any],
    prds_dir: Path | None = None,
) -> FormationManifest:
    """Write a new manifest under *orchestrator_run_path* and index it (FR03).

    Refuses when a manifest already exists: overwriting would discard joined
    members' recorded run paths, i.e. destroy the only link back to their
    evidence. Also refuses a ``prd_ids`` entry that already names a PRD file in
    *prds_dir* — allocating an identifier that is already taken is the collision
    this repository hit twice on 2026-09-04, and it is cheapest to refuse at
    allocation time. (Cross-member duplication is refused on EVERY load by the
    model; the on-disk check is allocation-only, because a member writing the
    PRD it was allocated must not later invalidate its own manifest.)

    PRD-FIX-149 review R10: the existence check and the write both happen under
    the SAME hold of the manifest lock ``_store`` already owns for every other
    manifest mutation -- otherwise two concurrent ``create`` calls for the same
    orchestrator run can both pass the ``exists()`` check before either writes,
    and the second silently clobbers the first's manifest instead of refusing.
    """
    manifest_path = manifest_path_for_run(orchestrator_run_path)
    with _exclusive(manifest_path):
        if manifest_path.exists():
            raise FormationError(
                f"a formation manifest already exists at {manifest_path}; revise it rather than overwriting it"
            )
        now = _now()
        data = dict(payload)
        data.setdefault("revision", 1)
        data.setdefault("created_utc", now)
        data.setdefault("updated_utc", now)
        data["orchestrator_run_path"] = str(orchestrator_run_path)
        try:
            manifest = FormationManifest.model_validate(data)
        except Exception as exc:
            raise FormationError(f"formation payload is invalid: {exc}") from exc
        if prds_dir is not None:
            _refuse_already_allocated_prd_ids(manifest, prds_dir)
        supplied = {str(m.get("member_id")): m for m in data.get("members", []) if isinstance(m, dict)}
        members, admitted, released = admitted_members(
            trw_dir,
            None,
            list(manifest.members),
            formation_id=manifest.formation_id,
            revision=manifest.revision,
            supplied=supplied,
        )
        manifest = manifest.model_copy(update={"members": members})
        commit_admissions(trw_dir, manifest.formation_id, orchestrator_run_path, admitted, released)
        write_manifest_locked(manifest_path, manifest)
        register_formation(trw_dir, manifest.formation_id, orchestrator_run_path)
    logger.info(
        "formation_created",
        formation_id=manifest.formation_id,
        members=len(manifest.members),
        manifest=str(manifest_path),
    )
    return manifest


def _refuse_already_allocated_prd_ids(manifest: FormationManifest, prds_dir: Path) -> None:
    if not prds_dir.is_dir():
        return
    on_disk: dict[str, str] = {}
    for path in prds_dir.glob("PRD-*.md"):
        parts = path.stem.split("-")
        if len(parts) >= 3:
            on_disk[f"{parts[0]}-{parts[1]}-{parts[2]}"] = path.name
    for member in manifest.members:
        for prd_id in member.prd_ids:
            existing = on_disk.get(prd_id)
            if existing:
                raise FormationError(
                    f"prd_ids entry {prd_id!r} allocated to member {member.member_id!r} already names a PRD "
                    f"on disk ({existing}); allocate an unused identifier block"
                )


def join(
    *,
    trw_dir: Path,
    formation_id: str,
    member_id: str,
    run_path: Path,
    pin_key: str | None,
    lock_timeout_seconds: float,
    candidate_id: str | None = None,
) -> FormationManifest:
    """Record *member_id*'s run and pin, atomically (FR04).

    A pending slot is joined only when it is ``open_join`` or admitted
    *candidate_id*, whose recorded pin and run must be this caller's (FR18).

    Idempotent for the same run path: a repeated join writes nothing and does
    NOT bump the revision, so a member that re-initialises cannot inflate the
    counter a concurrency test reads.
    """
    manifest_path = resolve_manifest_path(trw_dir, formation_id)
    with rewrite_manifest(manifest_path, timeout_seconds=lock_timeout_seconds) as box:
        manifest = box[0]
        member = manifest.member(member_id)
        recorded = member.run_path
        if recorded and Path(recorded) != run_path:
            raise FormationError(
                f"member {member_id!r} is already joined with run_path {recorded!r}; "
                f"refusing to rebind it to {str(run_path)!r} — that would orphan the first run's evidence"
            )
        if recorded and (pin_key is None or member.pin_key in (None, pin_key)):
            # NFR01 (PRD-FIX-149): the join that committed this slot may have died
            # before stamping the run; the retry is what finishes it.
            _restore_missing_stamp(
                run_path, formation_id=formation_id, member_id=member_id, member=member, pin_key=pin_key
            )
            return manifest
        if recorded:
            # PRD-CORE-274 FR14: the same run under a NEW pin. Only client lineage proven
            # by THIS process's own sibling adoption may rebind; lease expiry, knowing the
            # run path, or a persisted marker never do. R2 is an orchestrator revise().
            from trw_mcp.state._paths_pin_mgmt import adopted_from

            if adopted_from(str(pin_key)) != member.pin_key:
                raise FormationError(
                    f"rebind_not_authorized: member {member_id!r} is bound to another pin; a new pin is "
                    "accepted only through this process's client-lineage adoption or an orchestrator revise"
                )
            revised = manifest.model_copy(
                update={
                    "members": [
                        m if m.member_id != member_id else m.model_copy(update={"pin_key": pin_key})
                        for m in manifest.members
                    ],
                    "revision": manifest.revision + 1,
                    "updated_utc": _now(),
                }
            )
            box[0] = revised
            logger.info(
                "formation_member_rebound", formation_id=formation_id, member_id=member_id, path="client_lineage"
            )
            return revised
        require_join_admitted(trw_dir, member, candidate_id=candidate_id, run_path=run_path, pin_key=pin_key)
        members = [
            m
            if m.member_id != member_id
            else m.model_copy(
                update={
                    "run_path": str(run_path),
                    "pin_key": pin_key,
                    "status": FormationMemberStatus.JOINED.value,
                    "joined_utc": _now(),
                }
            )
            for m in manifest.members
        ]
        revised = manifest.model_copy(
            update={"members": members, "revision": manifest.revision + 1, "updated_utc": _now()}
        )
        box[0] = revised
    _stamp_run_record(run_path, formation_id=formation_id, member_id=member_id)
    logger.info("formation_member_joined", formation_id=formation_id, member_id=member_id, run=str(run_path))
    return revised


def _stamp_run_record(run_path: Path, *, formation_id: str, member_id: str) -> None:
    """Write the two ids onto the member's own ``run.yaml`` (FR04).

    Bidirectional by design: the manifest names the run, and the run names the
    formation, so a member run is self-describing and every adapter that starts
    from a run (the hook, the commit boundary, ``trw_status``) can find the
    manifest without being told which one.
    """
    from trw_mcp.state._run_yaml_update import update_run_yaml

    if not update_run_yaml(run_path, lambda data: data.update(formation_id=formation_id, member_id=member_id)):
        raise FormationError(f"member run {run_path} has no meta/run.yaml to stamp")


def _restore_missing_stamp(
    run_path: Path,
    *,
    formation_id: str,
    member_id: str,
    member: FormationMember,
    pin_key: str | None,
) -> None:
    """Stamp a joined run that carries NO formation stamp; never replace one.

    Only the interrupted-join window leaves a run with neither ids: a live stamp
    is already right, and a revoked one is FR18 compensation, which a retried
    join must not undo. Decided inside the run.yaml lock, so a concurrent
    revocation cannot slip between the check and the write.

    REPAIR IS AUTHORIZED, NOT MERELY MISSING (PRD-FIX-149 review R3). The
    caller already gates entry into this branch on the same two checks below,
    but a repair function that trusts its caller's gating is one refactor away
    from an unguarded resurrection: it re-derives its own authorization from
    *member* and *pin_key* rather than assuming the idempotent branch it was
    reached from did it correctly. A member the orchestrator has RETIRED
    (``abandoned``/``reassigned``) or self-reported ``delivered`` is a settled
    verdict, not a still-in-progress join a crash could have interrupted --
    repairing its stamp would let a stale retry resurrect membership the
    orchestrator already ended. The pin, when both this call's caller and the
    recorded member carry one, must also agree: an unpinned or mismatched
    retry is not proof this is the same session that joined.
    """
    if member.status not in (FormationMemberStatus.JOINED.value, FormationMemberStatus.ACTIVE.value):
        raise FormationError(
            f"member {member_id!r} is {member.status!r}, not joined or active; "
            "a retired member's run stamp is never repaired"
        )
    if pin_key is not None and member.pin_key is not None and pin_key != member.pin_key:
        raise FormationError(f"member {member_id!r} is pinned to a different session; refusing to repair its run stamp")
    from trw_mcp.state._run_yaml_update import update_run_yaml

    def _fill(data: dict[str, object]) -> None:
        if "formation_id" not in data and "revoked_formation_id" not in data:
            data.update(formation_id=formation_id, member_id=member_id)

    if not update_run_yaml(run_path, _fill):
        raise FormationError(f"member run {run_path} has no meta/run.yaml to repair its formation stamp")


def revise(
    *,
    trw_dir: Path,
    formation_id: str,
    caller_run_path: Path | None,
    updates: dict[str, dict[str, Any]],
    lock_timeout_seconds: float,
) -> FormationManifest:
    """Apply orchestrator-authored member updates (FR05).

    *updates* maps ``member_id`` to the fields to change. Refused unless
    *caller_run_path* IS the orchestrator run path — see the module docstring.
    """
    manifest_path = resolve_manifest_path(trw_dir, formation_id)
    with rewrite_manifest(manifest_path, timeout_seconds=lock_timeout_seconds) as box:
        manifest = box[0]
        _require_orchestrator(manifest, caller_run_path)
        members = list(manifest.members)
        for member_id, changes in updates.items():
            manifest.member(member_id)
            members = [m if m.member_id != member_id else _apply(m, changes) for m in members]
        members, admitted, released = admitted_members(
            trw_dir, manifest, members, formation_id=formation_id, revision=manifest.revision + 1, supplied=updates
        )
        revised = manifest.model_copy(
            update={"members": members, "revision": manifest.revision + 1, "updated_utc": _now()}
        )
        try:
            FormationManifest.model_validate(revised.model_dump(mode="json"))
        except Exception as exc:
            raise FormationError(f"revision would make the manifest invalid: {exc}") from exc
        commit_admissions(trw_dir, formation_id, Path(revised.orchestrator_run_path), admitted, released)
        box[0] = revised
    logger.info("formation_revised", formation_id=formation_id, revision=revised.revision)
    return revised


def _apply(member: FormationMember, changes: dict[str, Any]) -> FormationMember:
    try:
        return FormationMember.model_validate({**member.model_dump(mode="json"), **changes})
    except Exception as exc:
        raise FormationError(f"invalid update for member {member.member_id!r}: {exc}") from exc


def _require_orchestrator(manifest: FormationManifest, caller_run_path: Path | None) -> None:
    expected = Path(manifest.orchestrator_run_path)
    if caller_run_path is not None and caller_run_path.resolve() == expected.resolve():
        return
    raise FormationError(
        f"membership mutation of formation {manifest.formation_id!r} is accepted only from the orchestrator run "
        f"{expected}; the calling run is {caller_run_path or '(unresolved)'}. "
        "A role or payload assertion does not grant this authority."
    )


def mark_member_delivered(
    *,
    trw_dir: Path,
    formation_id: str,
    member_id: str,
    run_path: Path,
    lock_timeout_seconds: float,
) -> FormationManifest:
    """A member self-reports its OWN completion (FR11).

    Accepted only from the run recorded for *member_id* at join, and only for
    that member — a self-report is authority over yourself and nothing else. The
    FR11 gate independently re-checks that the run carries a delivery record, so
    this stamp alone can never make a member look finished.
    """
    manifest_path = resolve_manifest_path(trw_dir, formation_id)
    with rewrite_manifest(manifest_path, timeout_seconds=lock_timeout_seconds) as box:
        manifest = box[0]
        member = manifest.member(member_id)
        if not member.run_path or Path(member.run_path).resolve() != run_path.resolve():
            raise FormationError(
                f"member {member_id!r} may report delivery only from its joined run {member.run_path!r}; "
                f"got {str(run_path)!r}"
            )
        # Checked only after the joined-run authentication above, so neither
        # shortcut answers a foreign run. Retirement is the orchestrator's verdict
        # (FR05) and a self-report must not overwrite it; a repeat report changes
        # nothing, so it writes nothing (an unchanged box skips the rewrite).
        if member.status in (FormationMemberStatus.ABANDONED.value, FormationMemberStatus.REASSIGNED.value):
            raise FormationError(
                f"member {member_id!r} is {member.status} by orchestrator revision; "
                "its own delivery report cannot replace that verdict"
            )
        if member.status == FormationMemberStatus.DELIVERED.value:
            return manifest
        members = [
            m if m.member_id != member_id else m.model_copy(update={"status": FormationMemberStatus.DELIVERED.value})
            for m in manifest.members
        ]
        revised = manifest.model_copy(
            update={"members": members, "revision": manifest.revision + 1, "updated_utc": _now()}
        )
        box[0] = revised
    return revised
