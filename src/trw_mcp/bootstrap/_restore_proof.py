"""Remove a file only on positive proof, during an update's rollback and dirty-file restore (FB-01-KI1-RACE).

Belongs to the ``_update_transaction`` facade.

Both paths put the pre-run snapshot back, which used to start with an unconditional unlink and end with a copy
that overwrote whatever was at the name: bytes nobody in this run wrote (a concurrent writer, an editor saving
again, another update) were destroyed. Now a name is only ever:

* left as it is when it already holds the snapshot's bytes (``INTACT``: nothing to undo, nothing to race);
* otherwise captured into ``.trw/trash`` (``remove_if_hash``: rename, then re-verify) and named in a warning.
  A capture of exactly what THIS run wrote (``write_checkout_file`` ledger) is dropped only after a second
  re-hash in trash, so a writer holding an open descriptor that wrote after the move keeps its bytes (lead
  review r5 #3); the residual is a write landing between that re-hash and the drop;
* or KEPT, untouched and named, when none of that is possible (a FIFO, an unreadable file, a refused capture).

The copy-back creates a name only if it is absent (``O_CREAT|O_EXCL|O_NOFOLLOW``, no hard links, no staged
name), so a writer that lands after the name was cleared keeps its bytes (lead review N1/N2, codex r4 KI1b). Directories
are removed only when empty.
"""

from __future__ import annotations

import contextlib
import enum
import errno
import hashlib
import os
import re
import secrets
import shutil
import stat
import tempfile
from pathlib import Path

from trw_mcp._checkout_write import written_this_run

from ._refused_restore import UnsavedPreUpdate

__all__ = [
    "Cleared",
    "copy_back_exclusive",
    "put_back_or_preserve",
    "remove_proven_or_keep",
    "restore_snapshot_exclusive",
]


class Cleared(enum.Enum):
    CLEARED = "cleared"  # the name is free; the snapshot copy may be put there
    INTACT = "intact"  # the name already holds the snapshot's bytes
    KEPT = "kept"  # bytes that are not provably this run's: untouched, never copied over


def _sha_regular(path: Path) -> str | None:
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:  # trw-fail-silent-allow: no hash only means no proof
        return None


def _clear_own_in_place(dest: Path, expected: str, rel: str, why: str, notes: list[str]) -> Cleared:
    """Rename TRW's own write to an unguessable sibling, re-hash it there, and unlink it only on a match.

    Used only when the trash capture was refused: it frees space instead of needing it. The sibling name
    carries 64 random bits, so no other writer holds it (no check-then-act on a predictable name). Bytes that
    changed meanwhile are linked back if the name is free, else left at the sibling, which the note names.
    """
    aside = dest.with_name(f".{dest.name}.trw-{secrets.token_hex(8)}")
    try:
        os.rename(dest, aside)
    except FileNotFoundError:
        return Cleared.CLEARED
    except OSError as exc:  # trw-fail-silent-allow: not silent, the path is cleared or kept and named
        # A copy-on-write volume (APFS) needs space even to rename; only an unlink frees it. Last resort, full
        # disk only: re-hash and unlink. A writer landing between the two is the documented residual window.
        if exc.errno in (errno.ENOSPC, errno.EDQUOT) and _sha_regular(dest) == expected:
            dest.unlink()
            return Cleared.CLEARED
        notes.append(f"{rel}: TRW's own write could not be cleared, the disk is full ({why}; {exc}); left in place")
        return Cleared.KEPT
    if _sha_regular(aside) == expected:
        aside.unlink()
        return Cleared.CLEARED
    try:
        os.link(aside, dest)
        aside.unlink()
        notes.append(f"{rel}: changed while it was being cleared; the new bytes were left in place")
    except OSError:  # trw-fail-silent-allow: not silent, the leftover copy is named below
        notes.append(f"{rel}: changed while it was being cleared; that copy is at {aside}")
    return Cleared.KEPT


def remove_proven_or_keep(target_dir: Path, snapshot_root: Path, rel: str, notes: list[str]) -> Cleared:
    """Decide and act for one name; see the module docstring."""
    from ._trash import remove_if_hash

    dest = target_dir / rel
    try:
        mode = os.lstat(dest).st_mode
    except FileNotFoundError:
        return Cleared.CLEARED
    except OSError as exc:  # trw-fail-silent-allow: not silent, the path is kept and named
        notes.append(f"{rel}: left in place, it could not be inspected ({exc})")
        return Cleared.KEPT
    src = snapshot_root / rel
    if stat.S_ISLNK(mode):
        try:
            if src.is_symlink() and os.readlink(dest) == os.readlink(src):
                return Cleared.INTACT
        except OSError:  # trw-fail-silent-allow: an unreadable link is kept and named below
            pass
        notes.append(f"{rel}: left in place, a symbolic link this update cannot prove it made")
        return Cleared.KEPT
    if not stat.S_ISREG(mode):
        notes.append(f"{rel}: left in place, not a regular file")
        return Cleared.KEPT
    current = _sha_regular(dest)
    if current is None:
        notes.append(f"{rel}: left in place, it could not be read")
        return Cleared.KEPT
    if current == _sha_regular(src):
        try:  # same bytes: put the snapshot's permission bits back too (a chmod loses nothing)
            want = stat.S_IMODE(os.lstat(src).st_mode)
            if stat.S_IMODE(mode) != want:
                os.chmod(dest, want)
        except OSError:  # trw-fail-silent-allow: not silent, named below; the bytes are already the snapshot's
            notes.append(f"{rel}: content restored, but its permissions could not be put back")
        return Cleared.INTACT
    own = current == written_this_run(dest)
    outcome = remove_if_hash(dest, target_dir, current, key=rel)
    if outcome.status == "absent":
        return Cleared.CLEARED
    if outcome.status == "kept" and own:
        # The capture needs new space (a folder, a meta file); a full disk or an unusable trash refuses it.
        # TRW's own write is then cleared in place, which needs none (lead r5c #1, N8).
        return _clear_own_in_place(dest, current, rel, outcome.reason, notes)
    if outcome.status not in ("removed", "retained"):
        notes.append(f"{rel}: left in place ({outcome.reason})")
        return Cleared.KEPT
    # This run's own write, verified inside its private capture folder: only then is that folder dropped.
    # A writer that held the file open and wrote after the move fails this re-hash and stays in trash.
    captured = outcome.retained_at
    if own and outcome.status == "removed" and captured is not None and _sha_regular(captured) == current:
        from trw_memory._tree_removal import remove_tree

        remove_tree(captured.parent, purpose="verified capture of this run's own write")
        with contextlib.suppress(OSError):  # only an EMPTY trash folder goes; it existed for this capture alone
            captured.parent.parent.rmdir()
        return Cleared.CLEARED
    notes.append(f"{rel}: {_HELD}{_MOVED_TO} {outcome.retained_at or '.trw/trash'} {_PUT_BACK}")
    return Cleared.CLEARED


_HELD = "held bytes this update did not write; "
_MOVED_TO = "they were moved to"
_PUT_BACK = "before the earlier copy was put back"
_SAVED_AT = re.compile(r"your pre-update version is at .*", re.DOTALL)
_KEPT_ONLY_AT = re.compile(
    r"your pre-update version could not be saved in the project; it is kept only at .*", re.DOTALL
)
_DRY_RUN_NOTE = re.compile(re.escape(_HELD + _MOVED_TO) + " .*? " + re.escape(_PUT_BACK))


def as_dry_run_note(note: str) -> str:
    """Reword a restore note from a dry run's scratch copy: nothing was moved, and its trash path never existed."""
    note = _DRY_RUN_NOTE.sub(
        "a real run would find bytes here it did not write; they would be moved to a new folder under"
        " .trw/trash before the earlier copy is put back",
        note,
    )
    note = _SAVED_AT.sub("a real run would save your pre-update version under .trw/trash", note)
    return _KEPT_ONLY_AT.sub("a real run would have to keep your pre-update version outside the project", note)


def copy_back_exclusive(
    src: Path, dest: Path, rel: str, notes: list[str], *, add_mode: int = 0, narrow_by_umask: bool = False
) -> bool:
    """Put the snapshot copy *src* at *dest* only if *dest* is absent; never replace what is there.

    A file is created with ``O_CREAT|O_EXCL|O_NOFOLLOW`` and written through that descriptor: the create fails
    if anything (a file, a link, a planted symlink) holds the name, and it needs no hard links, so it works on
    exFAT, FAT32 and SMB (lead review r5 #1, #4). True when *dest* now holds the snapshot's bytes; ``OSError``
    only for a genuine I/O failure.
    """
    if src.is_symlink():
        try:
            os.symlink(os.readlink(src), dest)
        except FileExistsError:  # trw-fail-silent-allow: not silent; named, and the caller preserves the snapshot copy
            if dest.is_symlink() and os.readlink(dest) == os.readlink(src):
                return True
            notes.append(f"{rel}: a file appeared here during the restore and was kept")
            return False
        return True
    if not src.is_file():
        return True  # nothing restorable here, so nothing to lose
    dest.parent.mkdir(parents=True, exist_ok=True)
    info = os.lstat(src)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(dest, flags, stat.S_IMODE(info.st_mode))
    except FileExistsError:  # trw-fail-silent-allow: not silent; named, and the caller preserves the snapshot copy
        if _sha_regular(dest) == _sha_regular(src):
            return True
        notes.append(f"{rel}: a file appeared here during the restore and was kept")
        return False
    made = os.fstat(fd)
    # A new file (not a restore) follows the umask like the legacy write path; exec bits are added after.
    mode = (
        stat.S_IMODE(info.st_mode) & ~_current_umask() if narrow_by_umask else stat.S_IMODE(info.st_mode)
    ) | add_mode
    try:
        with os.fdopen(fd, "wb") as out, src.open("rb") as data:
            shutil.copyfileobj(data, out)
            out.flush()
            # Mode and times through the fd: a name swapped (symlink or hard link) after the create is never
            # touched (lead r7 c5, r8 G-L1).
            os.fchmod(out.fileno(), mode)
            os.utime(out.fileno(), ns=(info.st_atime_ns, info.st_mtime_ns))
    except BaseException:
        _unlink_if_ours(dest, made)  # never leave a half-written file at the user's name (lead r5c #2)
        raise
    return True


def _current_umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


def _unlink_if_ours(dest: Path, made: os.stat_result) -> None:
    """Unlink *dest* only while it is still the very file this call created (same device and inode)."""
    with contextlib.suppress(OSError):
        here = os.lstat(dest)
        if (here.st_dev, here.st_ino) == (made.st_dev, made.st_ino):
            dest.unlink()


def put_back_or_preserve(target_dir: Path, snapshot_root: Path, rel: str, notes: list[str]) -> bool:
    """Copy the snapshot's *rel* back; if that is refused for any reason, keep the snapshot copy in ``.trw/trash``.

    The snapshot is the only copy of the user's pre-update bytes, and the caller discards it afterwards, so a
    refused copy-back (a late writer holds the name, a KEPT file, an I/O error) must leave that copy somewhere
    named (lead, codex r2 lead #1): in ``.trw/trash``, else (an unusable trash) in a fresh folder beside the
    name. False when both fail (a full disk): the note says so, and the caller must keep the snapshot.
    """
    src = snapshot_root / rel
    for attempt in range(2):
        try:
            if copy_back_exclusive(src, target_dir / rel, rel, notes):
                return True
            break
        except OSError as exc:  # trw-fail-silent-allow: not silent; retried once, then named and preserved
            if attempt == 0 and exc.errno in (errno.ENOSPC, errno.EDQUOT):
                os.sync()  # a copy-on-write volume (APFS) reuses space freed by an unlink only once it commits
                continue
            notes.append(f"{rel}: the earlier copy could not be put back ({exc})")
            break
    if (keep := save_in_trash(target_dir, src, rel, notes)) is not None:
        notes.append(f"{rel}: your pre-update version is at {keep}")
        return True
    notes.append(f"{rel}: your pre-update version could not be saved in the project; it is kept only at {src}")
    return False


def save_in_trash(target_dir: Path, src: Path, rel: str, notes: list[str]) -> Path | None:
    """Copy *src* into a fresh folder in ``.trw/trash``, else beside *rel*'s name; None when neither has room."""
    for base in _save_places(target_dir, rel, notes):
        folder: Path | None = None
        try:
            base.mkdir(parents=True, exist_ok=True)
            folder = Path(tempfile.mkdtemp(prefix=".trw-pre-update-", dir=base))
            if copy_back_exclusive(src, folder / Path(rel).name, rel, notes):
                return folder / Path(rel).name
        except OSError:  # trw-fail-silent-allow: not silent; the next place is tried, then the caller names it
            if folder is not None:
                with contextlib.suppress(OSError):
                    folder.rmdir()  # only if empty: a half copy was already unlinked by copy_back_exclusive
    return None


def save_payload_in_trash(
    target_dir: Path, rel: str, notes: list[str], *, data: bytes = b"", mode: int = 0o644, link: str | None = None
) -> Path | None:
    """Like :func:`save_in_trash` for bytes (or a link target) already read through a descriptor."""
    for base in _save_places(target_dir, rel, notes):
        folder: Path | None = None
        try:
            base.mkdir(parents=True, exist_ok=True)
            folder = Path(tempfile.mkdtemp(prefix=".trw-pre-update-", dir=base))
            dest = folder / Path(rel).name
            if link is not None:
                os.symlink(link, dest)
            else:
                _create_with(dest, data, mode)
            return dest
        except OSError:  # trw-fail-silent-allow: not silent; the next place is tried, then the caller names it
            if folder is not None:
                with contextlib.suppress(OSError):
                    folder.rmdir()
    return None


def _create_with(dest: Path, data: bytes, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(dest, flags, mode)
    made = os.fstat(fd)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fchmod(out.fileno(), mode)
    except BaseException:
        _unlink_if_ours(dest, made)
        raise


def _save_places(target_dir: Path, rel: str, notes: list[str]) -> list[Path]:
    """``.trw/trash`` (unless it or ``.trw`` is a symlink, which would carry the copy out of the project), then
    the folder beside *rel*'s name."""
    from ._trash import trash_dir

    trash = trash_dir(target_dir)
    if any(p.is_symlink() for p in (trash.parent, trash)):
        notes.append(f"{rel}: .trw/trash is a symbolic link, so nothing is copied into it")  # lead r7 b6b
        return [(target_dir / rel).parent]
    return [trash, (target_dir / rel).parent]


def restore_snapshot_exclusive(target_dir: Path, snapshot_root: Path, intact: frozenset[str], notes: list[str]) -> None:
    """Copy every snapshot file back to an absent name, then drop update-created directories that are empty."""
    from trw_mcp.state.claude_md._sync_hash import _hash_file_path

    from ._update_transaction import (
        _TRANSACTION_DIRS,
        _TRANSACTION_FILES,
        _is_pruned_nested_dir,
        _snapshot_copy_ignore,
    )

    ignore = _snapshot_copy_ignore(snapshot_root, denied_is_marker=True)
    wanted: list[str] = [rel for rel in _TRANSACTION_FILES if os.path.lexists(snapshot_root / rel)]
    for top in _TRANSACTION_DIRS:
        base = snapshot_root / top
        if not base.is_dir() or base.is_symlink():
            continue
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            skipped = ignore(dirpath, [*dirnames, *filenames])
            dirnames[:] = [d for d in dirnames if d not in skipped and not Path(dirpath, d).is_symlink()]
            names = [*filenames, *(d for d in os.listdir(dirpath) if Path(dirpath, d).is_symlink())]
            wanted += [Path(dirpath, n).relative_to(snapshot_root).as_posix() for n in names if n not in skipped]
    unsaved = [  # an INTACT name already holds the snapshot's bytes
        rel for rel in sorted(set(wanted) - intact) if not put_back_or_preserve(target_dir, snapshot_root, rel, notes)
    ]
    for top in _TRANSACTION_DIRS:
        base = target_dir / top
        if not base.is_dir() or base.is_symlink():
            continue
        for dirpath, dirnames, _files in os.walk(base, topdown=False, followlinks=False):
            here = Path(dirpath)
            if _is_pruned_nested_dir(here, target_dir, denied_is_marker=True):
                continue
            if not (snapshot_root / here.relative_to(target_dir)).is_dir():
                try:
                    here.rmdir()  # only an EMPTY directory goes; any file in it stays
                except OSError:  # trw-fail-silent-allow: a non-empty or busy dir is simply kept
                    pass
            del dirnames
    _hash_file_path(target_dir / ".trw").unlink(missing_ok=True)
    if unsaved:  # every other path is done; the caller keeps the snapshot, the only copy of these
        raise UnsavedPreUpdate(errno.ENOSPC, unsaved)
