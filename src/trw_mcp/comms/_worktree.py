"""The member-bound writer of an FR17 worktree membership record (lane B's helper path).

Belongs to the ``trw_mcp.comms`` facade; re-exported there.

FR17 names two writers of the record that lets a linked worktree use the main
root: the orchestrator's admission of a worktree candidate (FR18) and "the lane-B
worktree helper while its member was bound under FR01 from the main root". This
is the second. The caller proves who it is by binding at the MAIN root, and it can
record only ITSELF, for a worktree git links to that same main root. Nothing the
caller passes names a member, a formation or a revision.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from trw_mcp.comms._identity import IdentityError, resolve_snapshot
from trw_mcp.formation import FormationError, linked_worktree, load, record_worktree_member

if TYPE_CHECKING:
    from fastmcp import Context


def record_own_worktree(worktree: Path, ctx: Context | None = None) -> dict[str, Any]:
    """Record *worktree* as the calling member's, or refuse with a closed reason."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir

    if not get_config().comms_enabled:
        return {"status": "disabled", "reason": "comms_disabled"}
    root, trw_dir = resolve_project_root(), resolve_trw_dir()
    if linked_worktree(root) is not None:
        return {"status": "refused", "reason": "not_main_root"}
    linked = linked_worktree(worktree)
    if linked is None or linked[0] != root.resolve():
        return {"status": "refused", "reason": "worktree_not_linked_here"}
    try:
        snapshot = resolve_snapshot(ctx, trw_dir=trw_dir, project_root=root)
        snapshot.assert_eligible()
        binding = snapshot.binding
        context = load(binding.run_path, trw_dir=trw_dir)
        if context is None:
            return {"status": "refused", "reason": "no_formation"}
        record = record_worktree_member(
            trw_dir,
            linked[1],
            formation_id=binding.formation_id,
            member_id=binding.member_id,
            manifest_revision=context.manifest.revision,
            creating_run=binding.run_path,
        )
    except IdentityError as exc:
        return {"status": "refused", "reason": exc.refusal.value}
    except FormationError:
        return {"status": "refused", "reason": "formation_unavailable"}
    return {"status": "ok", "member_id": record.member_id, "revision": record.revision}


__all__ = ["record_own_worktree"]
