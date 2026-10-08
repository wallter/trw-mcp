"""Refused restores: a user's pre-update bytes the rollback could not save in the project (FB-01-KI1-RACE).

Belongs to the ``_update_transaction`` facade.

The update snapshot lives in the TRW user directory (``update-snapshots/``), not ``$TMPDIR``: it is durable
(macOS purges ``$TMPDIR``) and has one stable location, so a later run can tell a real snapshot from anything
else (lead r7 MEDIUM 2). On a volume full to its free-space reserve (an APFS KI) the rollback keeps that
snapshot, names each file it could not put back with a copy command, and writes a record; the next
``update-project`` of that project moves those copies into ``.trw/trash`` and says so.
"""

from __future__ import annotations

import json
import os
import secrets
import shlex
import stat
import tempfile
import time
from pathlib import Path

import structlog
from trw_memory.safe_fs import write_beneath

from trw_mcp.bootstrap._snapshot_fd import (
    _DIR_FLAGS,
    _OWNER,
    _hold,
    _is_live,
    _missing_by_fd,
    _owner_by_fd,
    _walk,
    delete_verified,
    release_snapshot,
)

__all__ = [
    "SNAPSHOT_PREFIX",
    "UnsavedPreUpdate",
    "copy_now_lines",
    "is_durable_snapshot",
    "new_snapshot_dir",
    "record_refused",
    "release_snapshot",
    "retry_refused",
]

logger = structlog.get_logger(__name__)

_SUBDIR = "refused-restores"
_SNAPSHOTS = "update-snapshots"
SNAPSHOT_PREFIX = "trw-update-snapshot-"  # update-project's mkdtemp prefix; a retry trusts nothing else
_ORPHAN_AGE_S = 600  # a second guard: a snapshot younger than this is never treated as an orphan


class UnsavedPreUpdate(OSError):
    """The rollback finished every path but could not save these pre-update copies in the project."""

    def __init__(self, errno_: int, rels: list[str]) -> None:
        super().__init__(errno_, f"pre-update copies not saved in the project: {', '.join(rels)}")
        self.rels = rels


def copy_now_lines(root: Path, snapshot_root: Path, rels: list[str]) -> list[str]:
    """One message per file: where its only copy is and a never-clobber copy command.

    The project's volume is full when this prints, so the command says to free space first; when the home
    folder is on another volume it is offered as a destination that works right away (lead r6 MEDIUM b).
    """
    volume = _mount_point(root)
    home = Path.home()
    off_volume = _device(home) not in (None, _device(root))
    lines = []
    for rel in rels:
        src = shlex.quote(str(snapshot_root / rel))
        line = (
            f"your version of {rel} is only in {snapshot_root / rel}, outside the project: free some space on "
            f"{volume}, then run: cp -n {src} {shlex.quote(str(root / rel) + '.pre-update')}"
        )
        if off_volume:
            line += f" (or copy it off that volume now: cp -n {src} {shlex.quote(str(home / _home_name(rel)))})"
        lines.append(line)
    return lines


def _home_name(rel: str) -> str:
    return "trw-pre-update-" + rel.replace("/", "_")


def _device(path: Path) -> int | None:
    try:
        return os.stat(path).st_dev
    except OSError:  # trw-fail-silent-allow: unknown device only means no off-volume suggestion
        return None


def _mount_point(path: Path) -> Path:
    here = Path(os.path.realpath(path))
    while not os.path.ismount(here) and here != here.parent:
        here = here.parent
    return here


def _records_dir(*, create: bool) -> Path:
    from trw_mcp.state._user_paths import resolve_user_memory_dir

    return resolve_user_memory_dir(create=create).parent / _SUBDIR


def _snapshots_dir(*, create: bool) -> Path:
    from trw_mcp.state._user_paths import resolve_user_memory_dir

    return resolve_user_memory_dir(create=create).parent / _SNAPSHOTS


def new_snapshot_dir(project: Path | None = None) -> Path:
    """A fresh update snapshot folder under the TRW user directory; ``$TMPDIR`` only when that is unusable.

    The folder carries an owner marker naming *project*, so a retry record can only ever act on a snapshot
    of the project it names (lead r8 L2). A ``$TMPDIR`` fallback is never trusted by a record
    (:func:`is_durable_snapshot`); the caller tells the user it happened.
    """
    try:
        base = _snapshots_dir(create=True)
        base.mkdir(mode=0o700, exist_ok=True)
        snap = Path(tempfile.mkdtemp(prefix=SNAPSHOT_PREFIX, dir=base))
    except Exception as exc:  # trw-fail-silent-allow: not silent; logged, and the caller notes the fallback
        # UntrustedDirectoryError is not an OSError (lead r8 M1): any refusal of the user dir falls back.
        logger.warning("update_snapshot_in_tmpdir", reason=f"TRW user directory unusable: {exc}")
        snap = Path(tempfile.mkdtemp(prefix=SNAPSHOT_PREFIX))
    if project is not None:
        payload = json.dumps({"v": 1, "project": os.path.realpath(project)}).encode("utf-8")
        try:
            write_beneath(snap, _OWNER, payload, mode=0o600)
            _hold(snap)
        except BaseException:
            try:
                release_snapshot(snap)
            finally:
                from trw_memory._tree_removal import remove_tree

                remove_tree(snap, purpose="unfinished update snapshot")
            raise
    return snap


def is_durable_snapshot(snapshot: Path) -> bool:
    """True when *snapshot* sits in the TRW user ``update-snapshots`` folder (not a ``$TMPDIR`` fallback)."""
    try:
        return os.path.samefile(snapshot.parent, _snapshots_dir(create=False))
    except Exception:  # trw-fail-silent-allow: no usable snapshot folder means not durable
        return False


def record_refused(root: Path, snapshot_root: Path, rels: list[str]) -> Path | None:
    """Write the retry record; None (and the caller says so) when even the user directory refuses it."""
    payload = {"v": 1, "project": os.path.realpath(root), "snapshot": str(snapshot_root), "rels": rels}
    name = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{os.getpid()}-{secrets.token_hex(4)}.json"
    try:
        folder = _records_dir(create=True)
        folder.mkdir(mode=0o700, exist_ok=True)
        write_beneath(folder.parent, f"{_SUBDIR}/{name}", json.dumps(payload).encode("utf-8"), mode=0o600)
    except (OSError, ValueError):  # trw-fail-silent-allow: not silent; the caller reports a missing record
        return None
    return folder / name


def retry_refused(root: Path, notes: list[str]) -> None:
    """Move every recorded copy for *root* into ``.trw/trash``; drop the record once none is left to move.

    A record is untrusted input: it is acted on only when its schema is right and its snapshot is an update
    snapshot (``SNAPSHOT_PREFIX`` directly in the user ``update-snapshots`` folder, compared by identity, so a
    case or ``$TMPDIR`` difference never matters). Every read, move and delete goes through no-follow folder
    descriptors, so a swapped symlink cannot redirect it; only regular files and symlinks are moved; nothing is
    removed recursively. No error escapes: a record that fails is set aside and the update carries on.
    """
    try:
        records = sorted(_records_dir(create=False).glob("*.json"))
    except FileNotFoundError:  # trw-fail-silent-allow: no user directory yet means no records to retry
        records = []
    except Exception as exc:  # trw-fail-silent-allow: not silent; the user is told retries were skipped
        notes.append(f"earlier refused restores were not retried: the TRW user directory is unusable ({exc})")
        return
    project = os.path.realpath(root)
    named: set[str] = set()
    for record in records:
        parsed = _parse(record)
        if parsed is not None:
            named.add(str(parsed[1]))
        try:
            _retry_one(root, project, record, notes)
        except Exception as exc:  # trw-fail-silent-allow: not silent; set aside and named, the update goes on
            logger.warning("refused_restore_retry_failed", record=str(record), error=str(exc))
            _set_aside(record, f"could not be retried ({type(exc).__name__}: {exc})", notes)
    _name_orphans(project, named, notes)


def _name_orphans(project: str, named: set[str], notes: list[str]) -> None:
    """Name this project's snapshots that no record points at (lead r8 L5: on a full disk the record itself
    could not be written). They are named, never moved: without a record nothing says which files were refused.
    """
    try:
        base = _snapshots_dir(create=False)
        candidates = sorted(base.glob(SNAPSHOT_PREFIX + "*"))
    except Exception:  # trw-fail-silent-allow: no snapshot folder means no orphans to name
        return
    for snap in candidates:
        if str(snap) in named or snap.is_symlink() or not snap.is_dir():
            continue
        try:
            if time.time() - snap.stat().st_mtime < _ORPHAN_AGE_S or _owner(snap) != project:
                continue
            sfd = os.open(snap, _DIR_FLAGS)
            try:
                if _is_live(sfd):  # a running update's rollback snapshot (E2E-INC-143 B3)
                    continue
                files = _missing_by_fd(sfd, Path(project))
            finally:
                os.close(sfd)
        except Exception as exc:  # trw-fail-silent-allow: an unreadable snapshot is logged, not named
            logger.info("refused_restore_orphan_unreadable", snapshot=str(snap), error=str(exc))
            continue
        if not files:  # nothing here the project lacks: remove it (E2E-INC-143), after the same trust checks
            if _drop_snapshot(snap, project, notes):
                notes.append(f"removed {snap}, an earlier update's snapshot whose files are all back in the project")
            continue
        if files:
            shown = ", ".join(files[:5]) + (f" and {len(files) - 5} more" if len(files) > 5 else "")
            notes.append(
                f"an earlier update of this project left its pre-update copies in {snap} (no retry record could "
                f"be written): {shown}; copy what you need with cp -n, then delete that folder"
            )


def _owner(snap: Path) -> str | None:
    try:
        data = json.loads((snap / _OWNER).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # trw-fail-silent-allow: no readable marker means no owner
        return None
    owner = data.get("project") if isinstance(data, dict) else None
    return owner if isinstance(owner, str) else None


def _retry_one(root: Path, project: str, record: Path, notes: list[str]) -> None:
    parsed = _parse(record)
    if parsed is None:
        _set_aside(record, "is damaged", notes)
        return
    owner, snapshot, rels = parsed
    if owner != project:
        return
    if not _trusted(root, snapshot, rels):
        logger.warning("refused_restore_record_untrusted", record=str(record))
        notes.append(f"a retry record named {snapshot}, which is not an update snapshot; nothing there was touched")
        _set_aside(record, "was not trusted", notes)
        return
    # Anchored on the snapshots folder's own descriptor, then O_NOFOLLOW for the snapshot: no ancestor or leaf
    # symlink swapped in after the check can redirect it (lead r8 L1).
    rfd = os.open(_snapshots_dir(create=False), _DIR_FLAGS)
    try:
        if not _same(os.fstat(rfd), snapshot.parent, follow=True):
            notes.append(f"the update snapshot {snapshot} changed after it was checked; nothing was touched")
            return
        try:
            sfd = os.open(snapshot.name, _DIR_FLAGS, dir_fd=rfd)
        except FileNotFoundError:  # trw-fail-silent-allow: not silent; the record is dropped and the note says so
            notes.append(f"the update snapshot {snapshot} is gone; its retry record was dropped")
            record.unlink(missing_ok=True)
            return
    finally:
        os.close(rfd)
    try:
        if _owner_by_fd(sfd) != project:  # lead r8 L2: a record for A can never act on B's snapshot
            notes.append(f"a retry record named {snapshot}, which belongs to another project; nothing was touched")
            _set_aside(record, "named another project's snapshot", notes)
            return
        try:
            _move_recorded(root, snapshot, sfd, rels, record, notes)
        except Exception:
            left = [rel for rel in rels if os.path.lexists(snapshot / rel)]
            if left:  # lead r8 L4: the copies still there are named, with their paths
                notes.append("still in the update snapshot: " + ", ".join(str(snapshot / rel) for rel in left))
            raise
    finally:
        os.close(sfd)


def _set_aside(record: Path, why: str, notes: list[str]) -> None:
    """Rename *record* to a unique ``.bad`` name (never over an earlier one) and say truthfully what happened."""
    bad = record.with_name(f"{record.name}.bad.{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{secrets.token_hex(4)}")
    try:
        os.rename(record, bad)
    except OSError as exc:  # trw-fail-silent-allow: not silent; the failure itself is the note
        notes.append(f"a retry record {why}; it could not be set aside ({exc}) and is ignored: {record}")
        return
    notes.append(f"a retry record {why}; it was set aside as {bad.name}")


def _parse(record: Path) -> tuple[str, Path, list[str]] | None:
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # trw-fail-silent-allow: not silent; the caller sets the record aside, named
        return None
    if not isinstance(data, dict):
        return None
    owner, snapshot, rels = data.get("project"), data.get("snapshot"), data.get("rels")
    if not (isinstance(owner, str) and isinstance(snapshot, str) and isinstance(rels, list)):
        return None
    if not all(isinstance(rel, str) for rel in rels):
        return None
    return owner, Path(snapshot), rels


def _same(info: os.stat_result, path: Path, *, follow: bool = False) -> bool:
    try:
        here = os.stat(path) if follow else os.lstat(path)
    except OSError:  # trw-fail-silent-allow: a vanished path is simply not the same folder
        return False
    return (here.st_dev, here.st_ino) == (info.st_dev, info.st_ino)


def _trusted(root: Path, snapshot: Path, rels: list[str]) -> bool:
    if not snapshot.name.startswith(SNAPSHOT_PREFIX) or snapshot.is_symlink() or not snapshot.is_dir():
        return False
    try:
        if not os.path.samefile(snapshot.parent, _snapshots_dir(create=False)):
            return False
    except OSError:  # trw-fail-silent-allow: no snapshot folder means the record cannot name one
        return False
    project = Path(os.path.realpath(root))
    for rel in rels:
        if not _plain_rel(rel):
            return False
        folder = Path(os.path.realpath(project.joinpath(*rel.split("/")[:-1])))  # the project side stays inside
        if folder != project and project not in folder.parents:
            return False
    return True


def _plain_rel(rel: str) -> bool:
    raw = rel.split("/")
    return bool(rel) and "\0" not in rel and not rel.startswith("/") and all(p not in ("", ".", "..") for p in raw)


def _move_recorded(root: Path, snapshot: Path, sfd: int, rels: list[str], record: Path, notes: list[str]) -> None:
    left: list[str] = []
    for rel in rels:
        parts = rel.split("/")
        try:
            pfd = _walk(sfd, parts[:-1])
        except FileNotFoundError:  # trw-fail-silent-allow: not silent; named as gone below
            notes.append(f"{rel}: the pre-update copy in {snapshot} is gone; it could not be moved")
            continue
        try:
            outcome = _move_one(root, snapshot, pfd, parts[-1], rel, notes)
        finally:
            if pfd != sfd:
                os.close(pfd)
        if outcome is False:
            left.append(rel)
    if left:
        notes.extend(copy_now_lines(root, snapshot, left))
        return
    record.unlink(missing_ok=True)
    # Every recorded copy is handled. The snapshot goes only if nothing else in it is missing from the project:
    # keeping it would keep a copy of .trw/config.yaml (it can hold platform_api_key) forever (E2E-INC-143), but a
    # file the record did not cover (a folder rel, a damaged record) must never be dropped with it.
    left_over = _missing_by_fd(sfd, root)
    if left_over:
        shown = ", ".join(left_over[:5]) + (f" and {len(left_over) - 5} more" if len(left_over) > 5 else "")
        notes.append(f"kept {snapshot}: it still holds files the project does not: {shown}")
        return
    _drop_snapshot(snapshot, os.path.realpath(root), notes)


def _drop_snapshot(snapshot: Path, project: str, notes: list[str]) -> bool:
    """Remove *snapshot*: capture it first, re-prove it, then delete only through descriptors (E2E-INC-143 B2).

    The snapshot is renamed, relative to the snapshots folder's descriptor, into a fresh private capture folder;
    it is deleted only if the captured inode is the one checked and nothing in it is missing from the project,
    and then entry by entry through descriptors, never by pathname. Anything else puts it back and names it.
    """
    base = _snapshots_dir(create=False)
    where = snapshot  # where the snapshot is right now: the one place a failure note may name (codex r2 KI)
    try:
        rfd = os.open(base, _DIR_FLAGS)
        try:
            sfd = os.open(snapshot.name, _DIR_FLAGS, dir_fd=rfd)
            try:
                checked = os.fstat(sfd)
                if not (
                    snapshot.name.startswith(SNAPSHOT_PREFIX)
                    and _same(os.fstat(rfd), snapshot.parent, follow=True)
                    and _owner_by_fd(sfd) == project
                    and not _is_live(sfd)
                ):
                    return False
            finally:
                os.close(sfd)
            capture = f".trw-dropping-{secrets.token_hex(8)}"
            os.mkdir(capture, 0o700, dir_fd=rfd)  # by descriptor: a swapped folder path can never receive it
            cfd = os.open(capture, _DIR_FLAGS, dir_fd=rfd)
            try:
                os.rename(snapshot.name, "s", src_dir_fd=rfd, dst_dir_fd=cfd)
                where = base / capture
                moved = os.open("s", _DIR_FLAGS, dir_fd=cfd)
                try:
                    info = os.fstat(moved)
                    same = (info.st_dev, info.st_ino) == (checked.st_dev, checked.st_ino)
                    seen: set[tuple[int, int]] = set()
                    left = _missing_by_fd(moved, Path(project), seen=seen) if same else ["(not the checked snapshot)"]
                    if not left:  # emptied through the descriptor just verified, never by reopening "s"
                        delete_verified(cfd, moved, seen)
                finally:
                    os.close(moved)
                if left:
                    os.rename("s", snapshot.name, src_dir_fd=cfd, dst_dir_fd=rfd)  # back where it was, named
                    where = snapshot
                    notes.append(f"kept {snapshot}: it holds files the project does not: {', '.join(left[:5])}")
                    os.close(cfd)
                    cfd = -1
                    os.rmdir(capture, dir_fd=rfd)
                    return False
                os.rmdir("s", dir_fd=cfd)  # only an empty folder: one moved in under "s" keeps its files
            finally:
                if cfd != -1:
                    os.close(cfd)
            os.rmdir(capture, dir_fd=rfd)
        finally:
            os.close(rfd)
    except Exception as exc:  # trw-fail-silent-allow: not silent; the user is told where it is
        notes.append(f"the update snapshot could not be removed ({exc}); it is at {where}: check it, then delete it")
        return False
    return True


def _move_one(root: Path, snapshot: Path, pfd: int, name: str, rel: str, notes: list[str]) -> bool | None:
    """True moved, False kept for a later retry, None not a file to move (gone, or a folder or device)."""
    from ._restore_proof import save_payload_in_trash  # lazy: _restore_proof imports this module

    try:
        info = os.stat(name, dir_fd=pfd, follow_symlinks=False)
    except FileNotFoundError:  # trw-fail-silent-allow: not silent; named as gone below
        notes.append(f"{rel}: the pre-update copy in {snapshot} is gone; it could not be moved")
        return None
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):  # lead r7 b11: '.claude', '.', a FIFO
        logger.warning("refused_restore_rel_not_a_file", rel=rel)
        notes.append(f"{rel}: the retry record names something that is not a file; it was skipped")
        return None
    if stat.S_ISLNK(info.st_mode):
        kept = save_payload_in_trash(root, rel, notes, link=os.readlink(name, dir_fd=pfd))
    else:
        fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=pfd)
        with os.fdopen(fd, "rb") as data:
            payload = data.read()
        kept = save_payload_in_trash(root, rel, notes, data=payload, mode=stat.S_IMODE(info.st_mode))
    if kept is None:
        return False
    notes.append(f"{rel}: moved your pre-update version from {snapshot / rel} into {kept}")
    os.unlink(name, dir_fd=pfd)  # only what this retry just saved, by descriptor
    return True
