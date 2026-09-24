"""Orchestrator-only slot changes after creation: add pending slots, remove pending slots (ledger N3).

Belongs to the ``trw_mcp.formation`` facade; re-exported there.

A formation used to be fixed at creation: ``revise`` edits existing members only,
so a client arriving later (a new harness joining the swarm) needed a whole new
formation. These two verbs close that gap under the SAME authority as ``revise``
(the caller's run is the orchestrator run; no payload field grants it).

- ``add_slots`` appends pending members. A new slot may name ``admitted_candidate``;
  that runs through the same admission path as creation and revise (FR18): a live,
  unadmitted candidate only, the admitting revision stamped server-side, and the
  worktree record and candidate transition written under the manifest lock BEFORE
  the manifest.
- ``remove_slot`` removes a member that never joined. A joined or terminal member
  keeps its row: its run, pin and evidence are referenced by that row, and ending
  a membership is a status change (``revise``), not a deletion. An unjoined
  admission is released back to its candidate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from trw_mcp.formation._admission import admitted_members, commit_admissions
from trw_mcp.formation._manifest import FormationError, FormationManifest, FormationMemberStatus
from trw_mcp.formation._store import resolve_manifest_path, rewrite_manifest

logger = structlog.get_logger(__name__)


def _require_orchestrator(manifest: FormationManifest, caller_run_path: Path | None) -> None:
    expected = Path(manifest.orchestrator_run_path).resolve()
    if caller_run_path is None or caller_run_path.resolve() != expected:
        raise FormationError(
            f"slot changes to formation {manifest.formation_id!r} are accepted only from the orchestrator run "
            f"{expected}; the calling run is {caller_run_path or '(unresolved)'}"
        )


def _revised(manifest: FormationManifest, members: list[Any]) -> FormationManifest:
    data = manifest.model_dump(mode="json")
    data.update(
        members=[m.model_dump(mode="json") if hasattr(m, "model_dump") else m for m in members],
        revision=manifest.revision + 1,
        updated_utc=datetime.now(timezone.utc).isoformat(),
    )
    try:
        return FormationManifest.model_validate(data)
    except Exception as exc:
        raise FormationError(f"slot change would make the manifest invalid: {exc}") from exc


def add_slots(
    *,
    trw_dir: Path,
    formation_id: str,
    caller_run_path: Path | None,
    members: list[dict[str, Any]],
    lock_timeout_seconds: float,
) -> FormationManifest:
    """Append pending slots; each may carry ``admitted_candidate`` (never a run, pin or status)."""
    if not members:
        raise FormationError("add_slots needs at least one member")
    for payload in members:
        forbidden = {"run_path", "pin_key", "status", "joined_utc", "admitted_revision"} & set(payload)
        if forbidden:
            raise FormationError(f"a new slot is pending and server-stamped; it may not set {sorted(forbidden)}")
    manifest_path = resolve_manifest_path(trw_dir, formation_id)
    with rewrite_manifest(manifest_path, timeout_seconds=lock_timeout_seconds) as box:
        manifest = box[0]
        _require_orchestrator(manifest, caller_run_path)
        existing = {m.member_id for m in manifest.members}
        clashes = sorted(existing & {str(p.get("member_id")) for p in members})
        if clashes:
            raise FormationError(f"member ids already in formation {formation_id!r}: {clashes}")
        grown = _revised(manifest, [*manifest.members, *members])
        supplied = {str(p.get("member_id")): p for p in members}
        stamped, admitted, released = admitted_members(
            trw_dir,
            manifest,
            list(grown.members),
            formation_id=formation_id,
            revision=grown.revision,
            supplied=supplied,
        )
        revised = grown.model_copy(update={"members": stamped})
        commit_admissions(trw_dir, formation_id, Path(revised.orchestrator_run_path), admitted, released)
        box[0] = revised
    logger.info("formation_slots_added", formation_id=formation_id, revision=revised.revision, added=len(members))
    return revised


def remove_slot(
    *,
    trw_dir: Path,
    formation_id: str,
    caller_run_path: Path | None,
    member_id: str,
    lock_timeout_seconds: float,
) -> FormationManifest:
    """Remove a slot that never joined; a joined or terminal member is refused."""
    manifest_path = resolve_manifest_path(trw_dir, formation_id)
    with rewrite_manifest(manifest_path, timeout_seconds=lock_timeout_seconds) as box:
        manifest = box[0]
        _require_orchestrator(manifest, caller_run_path)
        member = manifest.member(member_id)
        if member.run_path is not None or str(member.status) != FormationMemberStatus.PENDING.value:
            raise FormationError(
                f"member {member_id!r} has joined or ended; end its membership with a status revise instead"
            )
        remaining = [m for m in manifest.members if m.member_id != member_id]
        if not remaining:
            raise FormationError("a formation keeps at least one member")
        revised = _revised(manifest, remaining)
        released = [member.admitted_candidate] if member.admitted_candidate else []
        commit_admissions(trw_dir, formation_id, Path(revised.orchestrator_run_path), [], released)
        box[0] = revised
    logger.info("formation_slot_removed", formation_id=formation_id, revision=revised.revision, member=member_id)
    return revised


__all__ = ["add_slots", "remove_slot"]
