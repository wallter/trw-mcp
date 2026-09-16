"""Trusted caller binding for comms (PRD-CORE-274-FR01, FR10 scope).

Formation is the sole membership authority; this module only *reads* it. It
answers one question — "which formation member is calling, if any?" — and
refuses in every case where the answer is not exactly one member.

Nothing here accepts identity from the caller. A tool argument naming a group,
sender, member, pin or incarnation cannot reach this module, because the only
inputs are the MCP ``Context`` and configuration. That is the FR01 property most
likely to be eroded by a well-meaning "just let the caller pass member_id", so
it is asserted by test rather than left to convention.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from trw_mcp.formation import (
    TERMINAL_STATUSES,
    FormationContext,
    FormationError,
    FormationManifest,
    FormationMember,
    load,
    manifest_path_for_run,
    resolve_manifest_path,
    stamped_ids,
)
from trw_mcp.state._call_context import build_call_context
from trw_mcp.state._paths_pin_mgmt import get_pinned_run

if TYPE_CHECKING:  # the annotation must match build_call_context's, without a runtime import
    from fastmcp import Context

#: Statuses that may use comms. Deliberately NOT "anything not terminal":
#: ``pending`` is a declared-but-never-joined member, and FR01 refuses it.
ELIGIBLE_STATUSES = frozenset({"joined", "active"})


class IdentityRefusal(str, Enum):
    """Closed refusal vocabulary for caller binding.

    Each value is a distinct operator-actionable cause. They are not collapsed
    into one "unauthorized" because the remedies differ: an unpinned run is
    fixed by pinning, an ambiguous match by repairing the manifest, and a
    terminal member not at all.
    """

    NO_PIN = "no_pinned_run"
    NO_FORMATION = "no_formation"
    NO_MATCH = "no_matching_member"
    AMBIGUOUS = "ambiguous_member_match"
    STAMP_MISMATCH = "stamped_identity_mismatch"
    UNCANONICAL = "uncanonical_formation_registration"
    NOT_ELIGIBLE = "member_not_eligible"
    UNAVAILABLE = "formation_unavailable"


class IdentityError(RuntimeError):
    """Raised when a caller cannot be bound to exactly one eligible member."""

    def __init__(self, refusal: IdentityRefusal, detail: str) -> None:
        super().__init__(f"{refusal.value}: {detail}")
        self.refusal = refusal
        self.detail = detail


@dataclass(frozen=True)
class CallerBinding:
    """The resolved answer: one member, one group, one project.

    ``group_id`` is derived (FR10) from the canonical project root and the
    canonical manifest path, never supplied. Two formations that share a name in
    different projects therefore cannot collide.
    """

    group_id: str
    formation_id: str
    member_id: str
    session_id: str
    run_path: Path
    manifest_path: Path
    is_orchestrator: bool


@dataclass(frozen=True)
class RecipientSnapshot:
    """Immutable recipient authority projected from the caller's same load."""

    member_id: str
    eligible: bool
    run_path: str | None
    pin_key: str | None
    #: The member's manifest-declared ownership globs, carried so that scoped
    #: addressing (PRD-CORE-276) resolves against the SAME trusted load that
    #: authorizes a direct send, rather than re-reading the manifest later.
    owned_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class CallerSnapshot:
    """Validated binding and the membership snapshot used by this operation.

    Binding is not eligibility: terminal members can prove who they are but
    may only cause an observed all-terminal closure, never access peer data.
    """

    binding: CallerBinding
    member_status: str
    all_terminal: bool
    recipients: tuple[RecipientSnapshot, ...]

    def assert_eligible(self) -> None:
        if self.member_status not in ELIGIBLE_STATUSES:
            raise IdentityError(IdentityRefusal.NOT_ELIGIBLE, "member is not joined or active")


def _canonical(path: Path) -> Path:
    """Refuse broken resolution, preserving non-existent ordinary peer paths.

    Strict resolution detects loops on Python 3.13+, whose non-strict resolver
    no longer raises for them. Missing ordinary paths retain prior semantics.
    Only filesystem-path resolution errors are translated, not caller logic.
    """
    try:
        try:
            return path.resolve(strict=True)
        except FileNotFoundError:
            return path.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise IdentityError(IdentityRefusal.UNAVAILABLE, "formation path cannot be resolved") from exc


def derive_group_id(project_root: Path, manifest_path: Path) -> str:
    """Derive the group id from canonical project + manifest paths (FR10).

    Both are resolved before hashing, so a symlinked or relative route to the
    same formation yields the same id, and the same formation *name* under a
    different project yields a different one.
    """

    canonical = f"{_canonical(project_root)}\n{_canonical(manifest_path)}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _assert_trusted_formation(run_path: Path, context: FormationContext, *, trw_dir: Path) -> None:
    """The formation the loader selected must be the one this run actually joined.

    ``resolve_active`` follows the run's stamped ``formation_id`` through the
    registry index and returns whatever manifest that entry points at, WITHOUT
    checking that the manifest carries that id. A stale or aliased index entry
    therefore binds a caller into a formation it never joined — reproduced
    end-to-end before this guard existed, not inferred from the source. Two
    checks close it:

    1. the stamped id equals the selected manifest's own id,
    2. the registry resolves that id back to this exact manifest path, and
    3. the manifest's OWN declared ``orchestrator_run_path`` canonically locates
       it where it was actually found.

    (3) is not implied by (2). The index and the loader consult the same pointer
    file, so they agree with each other by construction; neither consults the
    manifest's declaration of who owns it. A manifest naming a different owner
    while the index still points at the real file passed (1) and (2) and was
    accepted — found by a peer's reproducer, like the stamp case. The manifest,
    not the pointer index, is the membership authority, so its own declaration
    has to agree with where it lives.

    The owning branch reads its own manifest, but may also carry an actual
    joined-member stamp. All applicable stamp, registration and canonical-owner
    checks apply; the loader's member_id=None does not erase that stamp.

    This runs BEFORE any member is consulted, so an untrusted formation never
    gets to nominate a caller.
    """

    declared = context.manifest.formation_id
    stamped = stamped_ids(run_path)
    if stamped is not None and stamped[0] != declared:
        raise IdentityError(
            IdentityRefusal.STAMP_MISMATCH,
            f"run stamped formation {stamped[0]!r} but the resolved manifest declares {declared!r}",
        )
    try:
        registered = resolve_manifest_path(trw_dir, declared)
    except FormationError as exc:
        # Unregistered is a refusal, never a pass: an unregistered manifest has
        # no authority to name members.
        raise IdentityError(IdentityRefusal.UNCANONICAL, str(exc)) from exc
    if _canonical(registered) != _canonical(context.manifest_path):
        raise IdentityError(
            IdentityRefusal.UNCANONICAL,
            f"formation {declared!r} is registered at {registered}, not at {context.manifest_path}",
        )
    canonical = manifest_path_for_run(Path(context.manifest.orchestrator_run_path))
    if _canonical(canonical) != _canonical(context.manifest_path):
        raise IdentityError(
            IdentityRefusal.UNCANONICAL,
            f"formation {declared!r} declares owner run {context.manifest.orchestrator_run_path} "
            f"(canonically {canonical}) but was loaded from {context.manifest_path}",
        )


def _matching_members(manifest: FormationManifest, run_path: Path, session_id: str) -> list[FormationMember]:
    """Members whose recorded run_path AND pin_key both match this caller.

    BOTH must match (FR01). Matching on either alone would let a second session
    in the same run directory, or the same pin against a different run, bind to
    a member it does not own.
    """

    resolved = str(_canonical(run_path))
    matched: list[FormationMember] = []
    for member in manifest.members:
        if member.run_path is None or member.pin_key is None:
            continue
        if str(_canonical(Path(member.run_path))) == resolved and member.pin_key == session_id:
            matched.append(member)
    return matched


def _assert_stamp_consistent(member: FormationMember, context_member_id: str | None) -> None:
    """A stamped member id, when present, must agree with the matched member."""

    if context_member_id is not None and context_member_id != member.member_id:
        raise IdentityError(
            IdentityRefusal.STAMP_MISMATCH,
            f"run stamped member {context_member_id!r} but pin/run matched {member.member_id!r}",
        )


def resolve_snapshot(ctx: Context | None, *, trw_dir: Path, project_root: Path) -> CallerSnapshot:
    """Validate authority before exposing a fresh membership snapshot.

    No storage mutation occurs here. Only a uniquely bound, stamp-consistent
    caller can subsequently cause the facade to observe group closure.
    """

    call_context = build_call_context(ctx)
    session_id = call_context.session_id
    run_path = get_pinned_run(context=call_context)
    if run_path is None:
        raise IdentityError(IdentityRefusal.NO_PIN, "no pinned run for this session")

    try:
        formation_context = load(run_path, trw_dir=trw_dir)
    except FormationError as exc:  # unreadable/corrupt manifest must not look like "no formation"
        raise IdentityError(IdentityRefusal.UNAVAILABLE, str(exc)) from exc
    if formation_context is None:
        raise IdentityError(IdentityRefusal.NO_FORMATION, f"run {run_path} belongs to no formation")

    _assert_trusted_formation(run_path, formation_context, trw_dir=trw_dir)

    manifest = formation_context.manifest
    if not manifest.members:
        raise IdentityError(IdentityRefusal.NO_MATCH, "formation declares no members")

    matched = _matching_members(manifest, run_path, session_id)
    if not matched:
        raise IdentityError(
            IdentityRefusal.NO_MATCH,
            f"no joined member matches run {run_path} and session {session_id}",
        )
    if len(matched) > 1:
        names = ", ".join(sorted(m.member_id for m in matched))
        raise IdentityError(IdentityRefusal.AMBIGUOUS, f"{len(matched)} members match: {names}")

    member = matched[0]
    _assert_stamp_consistent(member, formation_context.member_id)
    # load() returns member_id=None for an owning run even if it joined as a
    # member. Consult the actual stamp too, not only the loader's projection.
    actual_stamp = stamped_ids(run_path)
    if actual_stamp is not None:
        _assert_stamp_consistent(member, actual_stamp[1])
    binding = CallerBinding(
        group_id=derive_group_id(project_root, formation_context.manifest_path),
        formation_id=manifest.formation_id,
        member_id=member.member_id,
        session_id=session_id,
        run_path=_canonical(run_path),
        manifest_path=formation_context.manifest_path,
        is_orchestrator=formation_context.is_orchestrator,
    )
    recipients = tuple(
        RecipientSnapshot(
            m.member_id,
            m.status in ELIGIBLE_STATUSES,
            str(_canonical(Path(m.run_path))) if m.run_path is not None else None,
            m.pin_key,
            tuple(m.owned_paths),
        )
        for m in manifest.members
    )
    return CallerSnapshot(
        binding, member.status, all(m.status in TERMINAL_STATUSES for m in manifest.members), recipients
    )


def resolve_caller(ctx: Context | None, *, trw_dir: Path, project_root: Path) -> CallerBinding:
    """Compatibility read-only binding plus eligibility; facade records closure."""
    snapshot = resolve_snapshot(ctx, trw_dir=trw_dir, project_root=project_root)
    snapshot.assert_eligible()
    return snapshot.binding
