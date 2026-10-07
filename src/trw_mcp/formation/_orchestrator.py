"""The addressable orchestrator member (PRD-CORE-340-FR18).

Belongs to the ``trw_mcp.formation`` facade; re-exported there.

The orchestrator owns the manifest but was never a member of it, so no peer could
``trw_send`` to it and its own ``trw_inbox`` refused ``no_matching_member``. The
join path already binds a session to a member by recording ``run_path`` +
``pin_key``, and comms identity reads exactly those two fields. So this module
does not add a parallel identity mechanism: it writes a member whose run path is
the orchestrator run and whose pin is the orchestrator session, at creation
(:func:`orchestrator_member`) or afterwards (:func:`add_orchestrator`).

The member carries NO authority. Authority stays structural: ``revise`` and the
slot verbs compare the caller's pinned run with ``orchestrator_run_path``, never a
role string, so ``role: orchestrator`` is a label, not a grant. It owns no globs,
tests or PRD ids, and :func:`is_orchestrator_slot` (run path equals the owning
run, again structural) lets the completion roll-up skip it, so the lead's
``trw_deliver`` never waits on itself.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.formation._manifest import (
    FormationError,
    FormationManifest,
    FormationMember,
    FormationMemberStatus,
)
from trw_mcp.formation._store import canonical_path, resolve_manifest_path, rewrite_manifest
from trw_mcp.state._factory_experiment import check

logger = structlog.get_logger(__name__)

__all__ = [
    "ORCHESTRATOR_ROLE",
    "add_orchestrator",
    "is_orchestrator_slot",
    "orchestrator_member",
    "require_factory_enabled",
]

ORCHESTRATOR_ROLE = "orchestrator"


def require_factory_enabled() -> None:
    """Refuse the orchestrator-member addition unless the experimental factory gate is enabled.

    The one guard for both ``create`` and ``add_orchestrator`` (PRD-CORE-340-FR11/FR12); callers
    invoke it before taking any lock, so a refusal writes nothing.
    """
    gate = check()
    if not gate.enabled:
        raise FormationError(f"the orchestrator member is experimental: {gate.reason} ({gate.message})")


def orchestrator_member(orchestrator_run_path: Path, member_id: str, pin_key: str | None) -> FormationMember:
    """A joined, authority-free member bound to the orchestrator's own run and pin."""
    if not pin_key:
        raise FormationError(
            "an orchestrator member needs the orchestrator session's pin key to be addressable; "
            "run this from the pinned orchestrator session"
        )
    try:
        return FormationMember(
            member_id=member_id,
            client=ORCHESTRATOR_ROLE,
            role=ORCHESTRATOR_ROLE,
            run_path=str(orchestrator_run_path),
            pin_key=pin_key,
            status=FormationMemberStatus.JOINED,
            joined_utc=datetime.now(timezone.utc).isoformat(),
        )
    except ValueError as exc:
        raise FormationError(f"orchestrator member is invalid: {exc}") from exc


def _bound_to_owner(manifest: FormationManifest, member: FormationMember) -> bool:
    if not member.run_path:
        return False
    return canonical_path(Path(member.run_path)) == canonical_path(Path(manifest.orchestrator_run_path))


def is_orchestrator_slot(manifest: FormationManifest, member: FormationMember) -> bool:
    """True for the orchestrator member: bound to the run that owns *manifest* AND labelled so.

    Both conditions, on purpose. A worker slot the orchestrator run itself joined is
    bound to that run but is ordinary work whose evidence the deliver gate must still
    see for an unverified caller, so the run path alone never excuses a slot.
    """
    return member.role == ORCHESTRATOR_ROLE and _bound_to_owner(manifest, member)


def add_orchestrator(
    *,
    trw_dir: Path,
    formation_id: str,
    caller_run_path: Path | None,
    member_id: str,
    pin_key: str | None,
    lock_timeout_seconds: float,
) -> FormationManifest:
    """Register the caller as the orchestrator member of an existing formation.

    Orchestrator-only (the caller's pinned run must own the manifest). Idempotent
    for the same id and pin: nothing is written and the revision does not move. The
    same member id under a NEW pin rebinds the orchestrator (its session's pin changed);
    a different member id is refused rather than replaced, and an
    id already used by any other member (a worker slot, even an unjoined admitted
    one) is refused; remove that slot first.
    """
    require_factory_enabled()
    manifest_path = resolve_manifest_path(trw_dir, formation_id)
    with rewrite_manifest(manifest_path, timeout_seconds=lock_timeout_seconds) as box:
        manifest = box[0]
        expected = Path(manifest.orchestrator_run_path)
        if caller_run_path is None or canonical_path(caller_run_path) != canonical_path(expected):
            raise FormationError(
                f"the orchestrator member of formation {formation_id!r} is registered only from the orchestrator "
                f"run {expected}; the calling run is {caller_run_path or '(unresolved)'}"
            )
        existing = next((m for m in manifest.members if _bound_to_owner(manifest, m)), None)
        rebound_from: str | None = None
        others = list(manifest.members)
        if existing is not None:
            if (
                existing.member_id == member_id
                and existing.pin_key == pin_key
                and is_orchestrator_slot(manifest, existing)
            ):
                return manifest
            if not is_orchestrator_slot(manifest, existing) or existing.member_id != member_id:
                # A worker slot the owner run joined is never rewritten into an orchestrator: that would drop its
                # ownership globs and PRD ids and exempt it from completion checks.
                raise FormationError(
                    f"formation {formation_id!r} already binds the orchestrator run to member "
                    f"{existing.member_id!r} (role {existing.role!r}, pin {existing.pin_key!r}); refusing to replace it"
                )
            # The orchestrator slot under a NEW pin: the lead session's MCP pin changed (feedback #144). The caller
            # run was proven above to own this manifest, and the CLI proved the new pin owns it. Only the pin moves.
            if not pin_key:
                orchestrator_member(expected, member_id, pin_key)  # raises the one named refusal
            rebound_from = existing.pin_key
            others = [m for m in manifest.members if m is not existing]
        if any(m.member_id == member_id for m in others):
            raise FormationError(
                f"member id {member_id!r} is already in formation {formation_id!r}; remove that slot "
                "or choose another id for the orchestrator"
            )
        member = (
            existing.model_copy(update={"pin_key": pin_key})
            if rebound_from is not None and existing is not None
            else orchestrator_member(expected, member_id, pin_key)
        )
        try:
            revised = FormationManifest.model_validate(
                {
                    **manifest.model_dump(mode="json"),
                    "members": [*(m.model_dump(mode="json") for m in others), member.model_dump(mode="json")],
                    "revision": manifest.revision + 1,
                    "updated_utc": datetime.now(timezone.utc).isoformat(),
                }
            )
        except ValueError as exc:
            raise FormationError(f"adding the orchestrator member would make the manifest invalid: {exc}") from exc
        box[0] = revised
    logger.info(
        "formation_orchestrator_added",
        formation_id=formation_id,
        member_id=member_id,
        rebound_from_pin=rebound_from,
    )
    return revised
