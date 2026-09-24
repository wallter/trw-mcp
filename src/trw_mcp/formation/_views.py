"""Read-only formation views: who owns a path, a member's brief, the status board.

Belongs to the ``trw_mcp.formation`` facade and is re-exported there. Moved out
of the facade whole (a cohesive read-only group) to keep ``__init__`` under the
350 effective-LOC gate. ``load``, ``settings`` and ``_project_root`` are looked
up on the facade at call time, so a suite that patches ``trw_mcp.formation.*``
keeps seeing its patch.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from trw_mcp.formation._brief import render_brief
from trw_mcp.formation._manifest import FormationError
from trw_mcp.formation._ownership import Ownership
from trw_mcp.formation._ownership import owner_of as _owner_of
from trw_mcp.formation._stall import StallFinding, stall_scan
from trw_mcp.formation._status import MemberRow, member_rows, non_terminal_members
from trw_mcp.formation._store import FormationContext

__all__ = ["FormationStatus", "brief", "owner_of", "status"]


@dataclass(frozen=True)
class FormationStatus:
    """The derived board: one row per member, plus the gate's waiting list."""

    formation_id: str
    manifest_path: str
    revision: int
    rows: list[MemberRow]
    non_terminal: list[tuple[str, str]]
    stalls: list[StallFinding] = field(default_factory=list)
    stall_measurement: str = "not_measured"
    mail_measurement: str = "not_measured"
    call_measurement: str = "not_measured"

    @property
    def stall_scope(self) -> dict[str, str]:
        """Name the conditions this stdio server cannot observe or page yet."""
        return {
            "mailbox": self.mail_measurement,
            "mcp_tool_calls": self.call_measurement,
            "external_calls": "not_measured",
            "paging": "not_measured",
        }


def _authority_project_root(manifest_path: Path, trw_dir: Path | None) -> Path:
    """Use the resolved formation's store, never the status caller's ambient cwd."""
    store = trw_dir.resolve() if trw_dir is not None else None
    if store is None or not manifest_path.is_relative_to(store):
        store = next((parent for parent in manifest_path.parents if parent.name == ".trw"), None)
    if store is None:
        raise ValueError("formation store cannot be derived from its manifest")
    return store.parent


def owner_of(
    path: str,
    *,
    run_path: Path | None = None,
    context: FormationContext | None = None,
    project_root: Path | None = None,
    trw_dir: Path | None = None,
) -> Ownership | None:
    """Owning member of *path*, or ``None`` when no formation is active (FR09/FR10)."""
    from trw_mcp import formation as facade

    resolved = context if context is not None else facade.load(run_path, trw_dir=trw_dir)
    if resolved is None:
        return None
    return _owner_of(resolved.manifest, path, project_root or facade._project_root())


def brief(
    member_id: str,
    *,
    run_path: Path | None = None,
    context: FormationContext | None = None,
    project_root: Path | None = None,
    trw_dir: Path | None = None,
) -> str:
    """Render *member_id*'s brief from the manifest (FR06)."""
    from trw_mcp import formation as facade

    resolved = context if context is not None else facade.load(run_path, trw_dir=trw_dir)
    if resolved is None:
        raise FormationError("no formation is active for this run; nothing to brief")
    return render_brief(resolved.manifest, member_id, project_root=project_root or facade._project_root())


def status(
    *,
    run_path: Path | None = None,
    context: FormationContext | None = None,
    trw_dir: Path | None = None,
) -> FormationStatus | None:
    """Read-only member roll-up, or ``None`` when no formation is active (FR07)."""
    from trw_mcp import formation as facade

    if context is None and trw_dir is None and run_path is not None:
        shared = facade.shared_authority_root()
        if shared is not None:
            shared_root, record = shared
            if facade.stamped_ids(run_path) == (record.formation_id, record.member_id):
                trw_dir = shared_root.trw_dir
    resolved = context if context is not None else facade.load(run_path, trw_dir=trw_dir)
    if resolved is None:
        return None
    knobs = facade.settings()
    try:
        root = _authority_project_root(resolved.manifest_path, trw_dir)
        scan = stall_scan(resolved.manifest, resolved.manifest_path, root, now=time.time())
        stalls = scan.findings
        mail_measurement, call_measurement = scan.mail_measurement, scan.call_measurement
    except (OSError, TypeError, ValueError):  # trw-fail-silent-allow: explicit not_measured status
        stalls, mail_measurement, call_measurement = [], "not_measured", "not_measured"
    stall_measurement = "measured" if mail_measurement == call_measurement == "measured" else "not_measured"
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
        stalls=stalls,
        stall_measurement=stall_measurement,
        mail_measurement=mail_measurement,
        call_measurement=call_measurement,
    )
