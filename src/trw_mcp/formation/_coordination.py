"""Coordination root for linked git worktrees (PRD-CORE-274-FR17).

Belongs to the ``trw_mcp.formation`` facade; re-exported there.

A linked worktree has its own ``.trw``, so without this every worktree member
resolved a different formation index, mailbox and group id (K5). The fix is NOT
a blanket ``resolve_project_root`` change: runs, pins, configuration and learnings
keep per-worktree resolution. Only the formation lookup, the mailbox beside the
manifest and the group id move to the main worktree root, and only as follows.

TWO KINDS OF OPERATION.
- Bootstrap (announce, withdraw, discover) grants nothing, so a caller in a linked
  worktree whose git back-pointer is consistent uses the main root. The worktree
  path it records is a locator; every candidate still needs orchestrator admission.
- Authority (enroll, send, fetch, ACK, status, join, pick-up) uses the main root
  only when the main root's store holds a WORKTREE MEMBERSHIP RECORD for this
  canonical worktree path AND the caller then binds under FR01 there. Otherwise it
  uses its own root, the pre-amendment behaviour.

Marker files, path equality and git common-directory identity are never
authority: the back-pointer check only decides whether the main root is even
CONSULTED, and the membership record is written only by an orchestrator admission
(FR18) or by a member bound from the main root (lane B's worktree helper).

The back-pointer must be consistent in both directions. ``<wt>/.git`` is a file
naming ``<common>/worktrees/<id>``, whose ``gitdir`` file names ``<wt>/.git``
back, and ``<common>`` is the non-bare ``<main>/.git``. That refuses another
repository, a submodule (``.git/modules/...``), a bare or nested repository, and a
copied, moved or deleted worktree (the back-pointer names the original path, or
the admin directory is gone).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from trw_mcp.formation._manifest import FormationError
from trw_mcp.formation._store import _exclusive, write_owner_only

logger = structlog.get_logger(__name__)
_RECORDS_RELATIVE = ("runtime", "worktree-members.json")
_GITDIR_PREFIX = "gitdir:"


@dataclass(frozen=True)
class CoordinationRoot:
    """Where formation lookups resolve. ``worktree`` is set only when shared."""

    project_root: Path
    trw_dir: Path
    worktree: Path | None = None


@dataclass(frozen=True)
class WorktreeRecord:
    worktree: str
    formation_id: str
    member_id: str
    manifest_revision: int
    creating_run: str
    revision: int


def _read_pointer(path: Path) -> Path | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        # trw-fail-silent-allow: an unreadable pointer only means "not a consistent linked worktree"
        return None
    if path.name == ".git":
        if not text.startswith(_GITDIR_PREFIX):
            return None
        text = text[len(_GITDIR_PREFIX) :].strip()
    target = Path(text)
    return (target if target.is_absolute() else path.parent / target).resolve()


def linked_worktree(project_root: Path) -> tuple[Path, Path] | None:
    """``(main_root, canonical_worktree)`` when *project_root* is a consistent linked worktree."""
    worktree = project_root.resolve()
    dotgit = worktree / ".git"
    if not dotgit.is_file():
        return None  # a main worktree, a nested repository's own .git dir, or no repository
    admin = _read_pointer(dotgit)
    if admin is None or admin.parent.name != "worktrees" or not admin.is_dir():
        return None  # a submodule (.git/modules/...) or a deleted worktree
    if _read_pointer(admin / "gitdir") != dotgit:
        return None  # copied or moved: the back-pointer names another checkout
    common = admin.parent.parent
    commondir = admin / "commondir"
    if commondir.is_file() and _read_pointer(commondir) != common:
        return None
    main = common.parent
    if common.name != ".git" or (main / ".git").resolve() != common or not common.is_dir():
        return None  # a bare repository has no main worktree to coordinate from
    return main, worktree


def _records_path(trw_dir: Path) -> Path:
    return trw_dir.joinpath(*_RECORDS_RELATIVE)


def _read_records(trw_dir: Path) -> dict[str, dict[str, Any]]:
    path = _records_path(trw_dir)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FormationError(f"worktree membership records {path} are unreadable: {exc}") from exc
    records = raw.get("records") if isinstance(raw, dict) else None
    if not isinstance(records, dict):
        raise FormationError(f"worktree membership records {path} are malformed")
    return {str(k): v for k, v in records.items() if isinstance(v, dict)}


def worktree_record(trw_dir: Path, worktree: Path) -> WorktreeRecord | None:
    """The membership record for *worktree* in the store at *trw_dir*, if any."""
    entry = _read_records(trw_dir).get(str(worktree.resolve()))
    if entry is None:
        return None
    try:
        return WorktreeRecord(
            worktree=str(worktree.resolve()),
            formation_id=str(entry["formation_id"]),
            member_id=str(entry["member_id"]),
            manifest_revision=int(entry["manifest_revision"]),
            creating_run=str(entry["creating_run"]),
            revision=int(entry["revision"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise FormationError(f"worktree membership record for {worktree} is malformed") from exc


def record_worktree_member(
    trw_dir: Path,
    worktree: Path,
    *,
    formation_id: str,
    member_id: str,
    manifest_revision: int,
    creating_run: Path,
) -> WorktreeRecord:
    """Write or replace the membership record, revisioned, under the store lock.

    Callers are the authority: the orchestrator's admission (FR18) or a member
    bound under FR01 from the main root. This function checks neither, which is
    why it is reachable only through those paths and never from a tool argument.
    """
    path = _records_path(trw_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(worktree.resolve())
    with _exclusive(path):
        records = _read_records(trw_dir)
        revision = int(records.get(key, {}).get("revision", 0)) + 1
        records[key] = {
            "formation_id": formation_id,
            "member_id": member_id,
            "manifest_revision": manifest_revision,
            "creating_run": str(creating_run),
            "revision": revision,
            "recorded_at": time.time(),
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        write_owner_only(tmp, (json.dumps({"records": records}, indent=2, sort_keys=True) + "\n").encode())
        os.replace(tmp, path)
    return WorktreeRecord(key, formation_id, member_id, manifest_revision, str(creating_run), revision)


def own_root() -> CoordinationRoot:
    """This process's own project root and store: where runs, pins and config resolve."""
    from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir

    return CoordinationRoot(resolve_project_root(), resolve_trw_dir())


def _store_name(root: CoordinationRoot) -> str | None:
    """The store's path relative to its project; None when the store lives outside it."""
    if not root.trw_dir.is_relative_to(root.project_root):
        return None
    return str(root.trw_dir.relative_to(root.project_root))


def bootstrap_root(own: CoordinationRoot | None = None) -> CoordinationRoot:
    """The root for announce, withdraw and discover: main when the back-pointer is consistent.

    A store outside its project (an explicit trw_dir override) has no main-root
    counterpart, so such a process always coordinates with itself.
    """
    own = own or own_root()
    name = _store_name(own)
    linked = linked_worktree(own.project_root) if name is not None else None
    if linked is None or name is None:
        return own
    main, worktree = linked
    return CoordinationRoot(main, main / name, worktree)


def shared_authority_root(own: CoordinationRoot | None = None) -> tuple[CoordinationRoot, WorktreeRecord] | None:
    """The main root plus this worktree's record, when both exist; None means "use your own root".

    The caller must still bind under FR01 at the returned root and match the
    record's formation and member; only then is the main root actually used.
    """
    root = bootstrap_root(own)
    if root.worktree is None:
        return None
    try:
        record = worktree_record(root.trw_dir, root.worktree)
    except FormationError:
        logger.info("formation_worktree_records_unreadable", store=str(root.trw_dir))
        # trw-fail-silent-allow: unreadable records are "no record" (own root); the cause is logged above
        return None
    return None if record is None else (root, record)


def authority_roots(own: CoordinationRoot | None = None) -> tuple[CoordinationRoot, ...]:
    """Distinct roots a caller may bind at: its FR17 bootstrap root, then its own root."""
    own = own or own_root()
    shared = bootstrap_root(own)
    return (shared,) if (shared.project_root, shared.trw_dir) == (own.project_root, own.trw_dir) else (shared, own)


__all__ = [
    "CoordinationRoot",
    "WorktreeRecord",
    "authority_roots",
    "bootstrap_root",
    "linked_worktree",
    "own_root",
    "record_worktree_member",
    "shared_authority_root",
    "worktree_record",
]
