"""Formation — one typed manifest for an orchestrator and its peer sessions.

THE BOUNDARY, STATED SO THE NEXT AUTHOR DOES NOT CROSS IT BY INCREMENT
(PRD-CORE-265 OQ-4). This package GOVERNS a formation: it owns the artifact
lifecycle — membership, ownership, evidence, delivery. It does not RUN one. It
starts no process, schedules nothing, dispatches no work, and holds no
background thread or timer. ``docs/VISION.md`` states that TRW does not compete
as a task-graph executor or agent runtime; a ``formation launch`` verb would
cross that line, and adding one is a vision decision, not a refactor.

THE FACADE. Everything outside this package imports from here and never from a
private sibling; a boundary test asserts the import direction. Nine verbs, of
which the six read/derive verbs are the declared interface —
:func:`load`, :func:`validate`, :func:`join`, :func:`owner_of`, :func:`brief`,
:func:`status`. Three more exist because FR03, FR05, and FR11 each mandate a
distinct WRITE that none of the six can express: :func:`create` (allocate a
formation), :func:`revise` (orchestrator-authenticated membership mutation), and
:func:`mark_member_delivered` (a member self-reporting its own completion).
Collapsing them into one parameterised verb would trade three explicit
authorisation contracts for one bag, which is the opposite of what FR05 asks
for.

FAIL-CLOSED, AND THE DISTINCTION THAT CARRIES IT (NFR02). Every verb answers
``None`` for "no formation is active" and raises :class:`FormationError` for
"a formation is active and something about it could not be read". Adapters MUST
keep those apart: conflating them reproduces the exact defect FR02 repairs,
where a surface that never checked reported the reassuring answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from trw_mcp.formation._brief import render_brief
from trw_mcp.formation._join import create as _create
from trw_mcp.formation._join import join as _join
from trw_mcp.formation._join import mark_member_delivered as _mark_member_delivered
from trw_mcp.formation._join import revise as _revise
from trw_mcp.formation._manifest import (
    TERMINAL_STATUSES as TERMINAL_STATUSES,
)
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
from trw_mcp.formation._ownership import Ownership as Ownership
from trw_mcp.formation._ownership import owner_of as _owner_of
from trw_mcp.formation._status import MemberRow as MemberRow
from trw_mcp.formation._status import member_rows, non_terminal_members
from trw_mcp.formation._store import (
    MANIFEST_FILENAME as MANIFEST_FILENAME,
)
from trw_mcp.formation._store import (
    FormationContext as FormationContext,
)
from trw_mcp.formation._store import manifest_path_for_run as manifest_path_for_run
from trw_mcp.formation._store import resolve_active

__all__ = [
    "MANIFEST_FILENAME",
    "TERMINAL_STATUSES",
    "FormationContext",
    "FormationError",
    "FormationManifest",
    "FormationMember",
    "FormationMemberStatus",
    "FormationSettings",
    "FormationStatus",
    "MemberRow",
    "Ownership",
    "brief",
    "create",
    "join",
    "load",
    "manifest_path_for_run",
    "mark_member_delivered",
    "owner_of",
    "revise",
    "settings",
    "status",
    "validate",
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


@dataclass(frozen=True)
class FormationStatus:
    """The derived board: one row per member, plus the gate's waiting list."""

    formation_id: str
    manifest_path: str
    revision: int
    rows: list[MemberRow]
    non_terminal: list[tuple[str, str]]


def settings() -> FormationSettings:
    """Resolve the typed knobs. Falls back to the FIELD DEFAULTS, never to
    hardcoded literals, so an unreadable config cannot silently disarm a gate."""
    from trw_mcp.models.config import TRWConfig, get_config

    try:
        cfg: object = get_config()
    except Exception:  # justified: an unreadable config must not crash an adapter
        cfg = TRWConfig()
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


def load(run_path: Path | None, *, trw_dir: Path | None = None) -> FormationContext | None:
    """The formation *run_path* belongs to, or ``None`` when it belongs to none."""
    return resolve_active(trw_dir or _trw_dir(), run_path)


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
    except Exception:  # justified: allocation check is skipped, never faked
        return None


def join(
    formation_id: str,
    member_id: str,
    run_path: Path,
    *,
    pin_key: str | None = None,
    trw_dir: Path | None = None,
) -> FormationManifest:
    """Record *member_id*'s run and pin atomically (FR04)."""
    return _join(
        trw_dir=trw_dir or _trw_dir(),
        formation_id=formation_id,
        member_id=member_id,
        run_path=run_path,
        pin_key=pin_key,
        lock_timeout_seconds=settings().lock_timeout_seconds,
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


def mark_member_delivered(run_path: Path, *, trw_dir: Path | None = None) -> FormationManifest | None:
    """A member self-reports its own delivery (FR11). ``None`` when not a member."""
    resolved_trw_dir = trw_dir or _trw_dir()
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


def owner_of(
    path: str,
    *,
    run_path: Path | None = None,
    context: FormationContext | None = None,
    project_root: Path | None = None,
    trw_dir: Path | None = None,
) -> Ownership | None:
    """Owning member of *path*, or ``None`` when no formation is active (FR09/FR10)."""
    resolved = context if context is not None else load(run_path, trw_dir=trw_dir)
    if resolved is None:
        return None
    return _owner_of(resolved.manifest, path, project_root or _project_root())


def brief(
    member_id: str,
    *,
    run_path: Path | None = None,
    context: FormationContext | None = None,
    project_root: Path | None = None,
    trw_dir: Path | None = None,
) -> str:
    """Render *member_id*'s brief from the manifest (FR06)."""
    resolved = context if context is not None else load(run_path, trw_dir=trw_dir)
    if resolved is None:
        raise FormationError("no formation is active for this run; nothing to brief")
    return render_brief(resolved.manifest, member_id, project_root=project_root or _project_root())


def status(
    *,
    run_path: Path | None = None,
    context: FormationContext | None = None,
    trw_dir: Path | None = None,
) -> FormationStatus | None:
    """Read-only member roll-up, or ``None`` when no formation is active (FR07)."""
    resolved = context if context is not None else load(run_path, trw_dir=trw_dir)
    if resolved is None:
        return None
    knobs = settings()
    return FormationStatus(
        formation_id=resolved.manifest.formation_id,
        manifest_path=str(resolved.manifest_path),
        revision=resolved.manifest.revision,
        rows=member_rows(
            resolved.manifest,
            member_limit=knobs.status_member_limit,
            pin_ttl_hours=knobs.pin_ttl_hours,
        ),
        non_terminal=non_terminal_members(resolved.manifest),
    )
