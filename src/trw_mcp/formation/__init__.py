"""Formation — one typed manifest for an orchestrator and its peer sessions.

THE BOUNDARY, STATED SO THE NEXT AUTHOR DOES NOT CROSS IT BY INCREMENT
(PRD-CORE-265 OQ-4). This package GOVERNS a formation: it owns the artifact
lifecycle — membership, ownership, evidence, delivery. It does not RUN one. It
starts no process, schedules nothing, dispatches no work, and holds no
background thread or timer. ``docs/VISION.md`` states that TRW does not compete
as a task-graph executor or agent runtime; a ``formation launch`` verb would
cross that line, and adding one is a vision decision, not a refactor.

THE FACADE. Everything outside this package imports from here and never from a
private sibling; a boundary test asserts the import direction. Read/derive
entrypoints include :func:`load`, :func:`validate`, :func:`owner_of`,
:func:`brief`, :func:`status`, and :func:`stall_scan`. Explicit writes
include :func:`create`, :func:`join`, :func:`revise`, and
:func:`mark_member_delivered`; collapsing their distinct authorisation contracts
into one parameterised verb would trade clarity for a bag. The scoped
``begin_call``/``start_call``/``clear_call`` seam lets the existing tool-call
wrapper record durable stall episodes without crossing a private import.

FAIL-CLOSED, AND THE DISTINCTION THAT CARRIES IT (NFR02). Every verb answers
``None`` for "no formation is active" and raises :class:`FormationError` for
"a formation is active and something about it could not be read". Adapters MUST
keep those apart: conflating them reproduces the exact defect FR02 repairs,
where a surface that never checked reported the reassuring answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog

from trw_mcp.formation._admission import revoke_run_stamp as revoke_run_stamp
from trw_mcp.formation._candidates import CANDIDATE_CAP as CANDIDATE_CAP
from trw_mcp.formation._candidates import LIVE_STATES as LIVE_CANDIDATE_STATES
from trw_mcp.formation._candidates import Candidate as Candidate
from trw_mcp.formation._candidates import CandidateError as CandidateError
from trw_mcp.formation._candidates import CandidateState as CandidateState
from trw_mcp.formation._candidates import announce as announce_candidate
from trw_mcp.formation._candidates import candidate as candidate
from trw_mcp.formation._candidates import candidate_for as candidate_for
from trw_mcp.formation._candidates import live_candidates as live_candidates
from trw_mcp.formation._candidates import set_state as set_candidate_state
from trw_mcp.formation._coordination import CoordinationRoot as CoordinationRoot
from trw_mcp.formation._coordination import WorktreeRecord as WorktreeRecord
from trw_mcp.formation._coordination import bootstrap_root as bootstrap_root
from trw_mcp.formation._coordination import linked_worktree as linked_worktree
from trw_mcp.formation._coordination import own_root as own_root
from trw_mcp.formation._coordination import record_worktree_member as record_worktree_member
from trw_mcp.formation._coordination import shared_authority_root as shared_authority_root
from trw_mcp.formation._coordination import worktree_record as worktree_record
from trw_mcp.formation._join import create as _create
from trw_mcp.formation._join import join as _join
from trw_mcp.formation._join import mark_member_delivered as _mark_member_delivered
from trw_mcp.formation._join import revise as _revise
from trw_mcp.formation._manifest import (
    TERMINAL_STATUSES as TERMINAL_STATUSES,
)
from trw_mcp.formation._manifest import AdmissionRefused as AdmissionRefused
from trw_mcp.formation._manifest import (
    FormationError as FormationError,
)
from trw_mcp.formation._manifest import (
    FormationManifest as FormationManifest,
)
from trw_mcp.formation._manifest import (
    FormationMember as FormationMember,
)
from trw_mcp.formation._manifest import (
    FormationMemberStatus as FormationMemberStatus,
)
from trw_mcp.formation._merge_queue import MergeItem as MergeItem
from trw_mcp.formation._merge_queue import MergeQueueError as MergeQueueError
from trw_mcp.formation._merge_queue import enqueue as merge_enqueue
from trw_mcp.formation._merge_queue import list_items as merge_list
from trw_mcp.formation._merge_queue import run_one as merge_run_one
from trw_mcp.formation._ownership import Ownership as Ownership
from trw_mcp.formation._ownership import declaration_covers as declaration_covers
from trw_mcp.formation._ownership import owner_of as owner_of_manifest
from trw_mcp.formation._ownership import relative_to_root as relative_to_root
from trw_mcp.formation._pause import PauseError as PauseError
from trw_mcp.formation._pause import PauseRecord as PauseRecord
from trw_mcp.formation._pause import ack as _ack_pause
from trw_mcp.formation._pause import orchestrator_run_of as orchestrator_run_of
from trw_mcp.formation._pause import pause as _pause
from trw_mcp.formation._pause import read_pause as read_pause
from trw_mcp.formation._pause import resume as _resume
from trw_mcp.formation._pause import roll_call as pause_roll_call
from trw_mcp.formation._slots import add_slots as _add_slots
from trw_mcp.formation._slots import remove_slot as _remove_slot
from trw_mcp.formation._stall import StallFinding as StallFinding
from trw_mcp.formation._stall import begin_call as begin_call
from trw_mcp.formation._stall import clear_call as clear_call
from trw_mcp.formation._stall import stall_scan as stall_scan
from trw_mcp.formation._stall import start_call as start_call
from trw_mcp.formation._status import MemberRow as MemberRow
from trw_mcp.formation._store import (
    MANIFEST_FILENAME as MANIFEST_FILENAME,
)
from trw_mcp.formation._store import (
    FormationContext as FormationContext,
)
from trw_mcp.formation._store import canonical_path as canonical_path
from trw_mcp.formation._store import manifest_path_for_run as manifest_path_for_run
from trw_mcp.formation._store import own_slot_if_caller as own_slot_if_caller
from trw_mcp.formation._store import read_manifest as read_manifest
from trw_mcp.formation._store import registered_formations as registered_formations
from trw_mcp.formation._store import resolve_active
from trw_mcp.formation._store import resolve_manifest_path as resolve_manifest_path
from trw_mcp.formation._store import stamped_ids as stamped_ids
from trw_mcp.formation._usage import formation_usage, member_usage
from trw_mcp.formation._views import FormationStatus as FormationStatus
from trw_mcp.formation._views import brief as brief
from trw_mcp.formation._views import owner_of as owner_of
from trw_mcp.formation._views import status as status

logger = structlog.get_logger(__name__)

__all__ = [
    "CANDIDATE_CAP",
    "LIVE_CANDIDATE_STATES",
    "MANIFEST_FILENAME",
    "TERMINAL_STATUSES",
    "AdmissionRefused",
    "Candidate",
    "CandidateError",
    "CandidateState",
    "CoordinationRoot",
    "FormationContext",
    "FormationError",
    "FormationManifest",
    "FormationMember",
    "FormationMemberStatus",
    "FormationSettings",
    "FormationStatus",
    "MemberRow",
    "MergeItem",
    "MergeQueueError",
    "Ownership",
    "PauseError",
    "PauseRecord",
    "StallFinding",
    "WorktreeRecord",
    "ack_pause",
    "add_slots",
    "announce_candidate",
    "authority_trw_dir",
    "begin_call",
    "bootstrap_root",
    "brief",
    "candidate",
    "candidate_for",
    "canonical_path",
    "clear_call",
    "create",
    "declaration_covers",
    "formation_usage",
    "join",
    "linked_worktree",
    "live_candidates",
    "load",
    "manifest_path_for_run",
    "mark_member_delivered",
    "member_usage",
    "merge_enqueue",
    "merge_list",
    "merge_run_one",
    "orchestrator_run_of",
    "own_root",
    "own_slot_if_caller",
    "owner_of",
    "owner_of_manifest",
    "pause",
    "pause_roll_call",
    "read_manifest",
    "read_pause",
    "record_worktree_member",
    "registered_formations",
    "relative_to_root",
    "remove_slot",
    "resolve_manifest_path",
    "resume",
    "revise",
    "revoke_run_stamp",
    "set_candidate_state",
    "settings",
    "shared_authority_root",
    "stall_scan",
    "stamped_ids",
    "start_call",
    "status",
    "validate",
    "worktree_record",
]


@dataclass(frozen=True)
class FormationSettings:
    """The five typed knobs, resolved once per call site."""

    ownership_enforcement: str
    hook_ownership_mode: str
    deliver_gate: str
    status_member_limit: int
    lock_timeout_seconds: float
    pin_ttl_hours: int


def settings() -> FormationSettings:
    """Resolve the typed knobs. Falls back to the FIELD DEFAULTS, never to
    hardcoded literals, so an unreadable config cannot silently disarm a gate.

    ``get_config()`` already owns the fail-open/fail-closed decision (a
    malformed ``config.yaml`` logs and returns ``TRWConfig()`` defaults, unless
    ``TRW_CONFIG_STRICT`` asks it to re-raise instead). A second broad catch
    HERE would silently swallow that re-raise and hand back defaults anyway —
    exactly the "missing config mixin silently disarms five gates" defect an
    audit flagged 2026-09-04 — so this call is unguarded and lets strict mode's
    signal reach its caller.
    """
    from trw_mcp.models.config import get_config

    cfg: object = get_config()
    return FormationSettings(
        ownership_enforcement=str(getattr(cfg, "formation_ownership_enforcement", "refuse")),
        hook_ownership_mode=str(getattr(cfg, "formation_hook_ownership_mode", "warn")),
        deliver_gate=str(getattr(cfg, "formation_deliver_gate", "block")),
        status_member_limit=int(getattr(cfg, "formation_status_member_limit", 16)),
        lock_timeout_seconds=float(getattr(cfg, "formation_manifest_lock_timeout_seconds", 10.0)),
        pin_ttl_hours=int(getattr(cfg, "pin_ttl_hours", 24)),
    )


def _trw_dir() -> Path:
    from trw_mcp.state._paths import resolve_trw_dir

    return resolve_trw_dir()


def _project_root() -> Path:
    from trw_mcp.state._paths import resolve_project_root

    return resolve_project_root()


def authority_trw_dir(run_path: Path | None, trw_dir: Path | None = None) -> Path | None:
    """The store that holds *run_path*'s formation: *trw_dir* when given, else
    the main root's store for a recorded linked-worktree member, else ``None``
    (the caller's own store). A worktree's own index never lists the main
    root's formation; an orchestrator run, or a formation the own index does
    list, stays on the own store.
    """
    if trw_dir is not None or run_path is None or manifest_path_for_run(run_path).is_file():
        return trw_dir
    shared = shared_authority_root()
    if shared is None:
        return None
    root, record = shared
    if stamped_ids(run_path) != (record.formation_id, record.member_id):
        return None
    try:
        listed_locally = record.formation_id in registered_formations(_trw_dir())
    except (
        FormationError
    ):  # trw-fail-silent-allow: an unreadable own index cannot hold the formation; the main root is the only candidate
        listed_locally = False
    return None if listed_locally else root.trw_dir


def load(run_path: Path | None, *, trw_dir: Path | None = None) -> FormationContext | None:
    """The formation *run_path* belongs to, or ``None`` when it belongs to none."""
    return resolve_active(authority_trw_dir(run_path, trw_dir) or _trw_dir(), run_path)


def validate(data: dict[str, object]) -> FormationManifest:
    """Validate a manifest payload, raising :class:`FormationError` on refusal."""
    try:
        return FormationManifest.model_validate(data)
    except FormationError:
        raise
    except Exception as exc:
        raise FormationError(f"formation manifest is invalid: {exc}") from exc


def create(
    orchestrator_run_path: Path,
    payload: dict[str, object],
    *,
    trw_dir: Path | None = None,
    prds_dir: Path | None = None,
) -> FormationManifest:
    """Allocate a formation under *orchestrator_run_path* (FR03)."""
    return _create(
        trw_dir=trw_dir or _trw_dir(),
        orchestrator_run_path=orchestrator_run_path,
        payload=dict(payload),
        prds_dir=prds_dir if prds_dir is not None else _default_prds_dir(),
    )


def _default_prds_dir() -> Path | None:
    try:
        from trw_mcp.models.config import get_config

        return _project_root() / str(get_config().prds_relative_path)
    except Exception as exc:
        # PRD-id allocation validation is skipped, never faked: `create()` gets
        # `prds_dir=None`, which `_create` treats as "cannot verify", not as
        # "every id is valid".
        logger.debug("formation_prds_dir_unresolved", reason=str(exc))
        return None


def join(
    formation_id: str,
    member_id: str,
    run_path: Path,
    *,
    pin_key: str | None = None,
    trw_dir: Path | None = None,
    candidate_id: str | None = None,
) -> FormationManifest:
    """Record *member_id*'s run and pin atomically (FR04); a pending slot needs admission (FR18)."""
    return _join(
        trw_dir=trw_dir or _trw_dir(),
        formation_id=formation_id,
        member_id=member_id,
        run_path=run_path,
        pin_key=pin_key,
        lock_timeout_seconds=settings().lock_timeout_seconds,
        candidate_id=candidate_id,
    )


def revise(
    formation_id: str,
    caller_run_path: Path | None,
    updates: dict[str, dict[str, object]],
    *,
    trw_dir: Path | None = None,
) -> FormationManifest:
    """Apply orchestrator-authenticated membership changes (FR05)."""
    return _revise(
        trw_dir=trw_dir or _trw_dir(),
        formation_id=formation_id,
        caller_run_path=caller_run_path,
        updates={k: dict(v) for k, v in updates.items()},
        lock_timeout_seconds=settings().lock_timeout_seconds,
    )


def add_slots(
    formation_id: str,
    caller_run_path: Path | None,
    members: list[dict[str, object]],
    *,
    trw_dir: Path | None = None,
) -> FormationManifest:
    """Append pending slots, optionally admitting a candidate to each (orchestrator only; ledger N3)."""
    return _add_slots(
        trw_dir=trw_dir or _trw_dir(),
        formation_id=formation_id,
        caller_run_path=caller_run_path,
        members=[dict(m) for m in members],
        lock_timeout_seconds=settings().lock_timeout_seconds,
    )


def remove_slot(
    formation_id: str,
    caller_run_path: Path | None,
    member_id: str,
    *,
    trw_dir: Path | None = None,
) -> FormationManifest:
    """Remove a slot that never joined (orchestrator only; ledger N3)."""
    return _remove_slot(
        trw_dir=trw_dir or _trw_dir(),
        formation_id=formation_id,
        caller_run_path=caller_run_path,
        member_id=member_id,
        lock_timeout_seconds=settings().lock_timeout_seconds,
    )


def pause(
    formation_id: str,
    caller_run_path: Path | None,
    reason: str,
    *,
    until_utc: str | None = None,
    trw_dir: Path | None = None,
) -> PauseRecord:
    """Pause the formation (orchestrator only; PAUSE-RESUME-DESIGN rev 2)."""
    return _pause(
        trw_dir=trw_dir or _trw_dir(),
        formation_id=formation_id,
        caller_run_path=caller_run_path,
        reason=reason,
        until_utc=until_utc,
        lock_timeout_seconds=settings().lock_timeout_seconds,
    )


def resume(formation_id: str, caller_run_path: Path | None, *, trw_dir: Path | None = None) -> str:
    """End the active pause (orchestrator only); returns its pause_id."""
    return _resume(
        trw_dir=trw_dir or _trw_dir(),
        formation_id=formation_id,
        caller_run_path=caller_run_path,
        lock_timeout_seconds=settings().lock_timeout_seconds,
    )


def ack_pause(manifest_path: Path, member_id: str, member_run_path: Path, pause_id: str) -> bool:
    """Record a bound member's own ack of *pause_id*; False when already recorded."""
    return _ack_pause(
        manifest_path=manifest_path,
        member_id=member_id,
        member_run_path=member_run_path,
        pause_id=pause_id,
        lock_timeout_seconds=settings().lock_timeout_seconds,
    )


def mark_member_delivered(run_path: Path, *, trw_dir: Path | None = None) -> FormationManifest | None:
    """A member self-reports its own delivery (FR11). ``None`` when not a member."""
    resolved_trw_dir = authority_trw_dir(run_path, trw_dir) or _trw_dir()
    context = resolve_active(resolved_trw_dir, run_path)
    if context is None or context.member_id is None:
        return None
    return _mark_member_delivered(
        trw_dir=resolved_trw_dir,
        formation_id=context.manifest.formation_id,
        member_id=context.member_id,
        run_path=run_path,
        lock_timeout_seconds=settings().lock_timeout_seconds,
    )
