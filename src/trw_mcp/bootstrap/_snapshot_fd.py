"""Descriptor-only helpers for update snapshots: the liveness lock, owner marker, byte compare and delete.

Split from ``_refused_restore`` (E2E-INC-143); nothing here follows a symlink or opens a path by name outside
a descriptor the caller already checked.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no flock
    fcntl = None  # type: ignore[assignment]

_OWNER = ".trw-update-snapshot.json"  # the snapshot's owner marker (outside every transaction path)
_LOCK = ".trw-update-snapshot.lock"  # held (flock) by the running update for its whole life: the liveness proof
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
_HELD: dict[str, int] = {}  # snapshot path -> the lock descriptor this process holds


def _hold(snap: Path) -> None:
    """Take this update's liveness lock on *snap*; the OS releases it if the process dies (E2E-INC-143 B3)."""
    if fcntl is None:  # no flock (Windows): orphans are then never removed, only named
        return
    fd = os.open(snap / _LOCK, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(fd)
        raise
    _HELD[str(snap)] = fd


def release_snapshot(snap: Path) -> None:
    """Drop this update's liveness lock on *snap* (before it removes the snapshot, or keeps it after a refusal)."""
    fd = _HELD.pop(str(snap), None)
    if fd is not None:
        os.close(fd)


def _is_live(sfd: int) -> bool:
    """True while an update still holds *sfd*'s liveness lock, or when liveness cannot be proven either way."""
    if fcntl is None:
        return True
    try:
        fd = os.open(_LOCK, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=sfd)
    except FileNotFoundError:  # trw-fail-silent-allow: a snapshot that never had a lock (pre-INC-143) is not live
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:  # trw-fail-silent-allow: held by a running update, which is the answer
        return True
    finally:
        os.close(fd)
    return False


def _owner_by_fd(sfd: int) -> str | None:
    try:
        fd = os.open(_OWNER, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=sfd)
        with os.fdopen(fd, "rb") as raw:
            data = json.loads(raw.read().decode("utf-8"))
    except (OSError, ValueError):  # trw-fail-silent-allow: no readable marker means no owner (not trusted)
        return None
    owner = data.get("project") if isinstance(data, dict) else None
    return owner if isinstance(owner, str) else None


Seen = set[tuple[int, int]]  # (st_dev, st_ino) of every entry the byte compare checked


def _ident(info: os.stat_result) -> tuple[int, int]:
    return (info.st_dev, info.st_ino)


class KeptChanged(OSError):
    """Some entries changed between the compare and the delete; they were kept, never deleted."""


def delete_verified(cfd: int, moved: int, seen: Seen) -> None:
    """Empty the verified capture *moved* (``s`` under the capture folder *cfd*) of exactly what the compare checked.

    Each file is first moved into a private quarantine folder ``q`` under random names, checked there, and unlinked
    only if it is still an entry the compare read: a file or folder put under one of the snapshot's names after the
    compare, even at the instant before the delete, ends up kept, never deleted (E2E-INC-143 r3, codex r2 block,
    luna major). Raises :class:`KeptChanged` when anything was kept; ``q`` then holds it.
    """
    os.mkdir("q", 0o700, dir_fd=cfd)
    qfd = os.open("q", _DIR_FLAGS, dir_fd=cfd)
    try:
        kept = _empty_fd(moved, seen, qfd)
    finally:
        os.close(qfd)
    if kept:
        raise KeptChanged(f"{kept} item(s) changed while being removed (gone, or kept in its s and q folders)")
    os.rmdir("q", dir_fd=cfd)


def _empty_fd(dfd: int, seen: Seen, qfd: int) -> int:
    """Delete the checked entries inside the verified folder *dfd*; return how many entries were kept instead."""
    kept = 0
    for entry in list(os.scandir(dfd)):
        if entry.is_dir(follow_symlinks=False):
            sub = os.open(entry.name, _DIR_FLAGS, dir_fd=dfd)
            try:
                checked = _ident(os.fstat(sub)) in seen
                kept += _empty_fd(sub, seen, qfd) if checked else 1
            finally:
                os.close(sub)
            if checked:
                os.rmdir(entry.name, dir_fd=dfd)  # refuses a folder that still holds anything
            continue
        parked = _reserve(qfd)
        try:  # replaces only the empty placeholder this call just created exclusively (luna r2)
            os.rename(entry.name, parked, src_dir_fd=dfd, dst_dir_fd=qfd)
        except OSError:  # trw-fail-silent-allow: not silent, counted as changed (gone, or no longer a file)
            os.unlink(parked, dir_fd=qfd)
            kept += 1
            continue
        if _ident(os.stat(parked, dir_fd=qfd, follow_symlinks=False)) in seen:
            os.unlink(parked, dir_fd=qfd)
        else:
            kept += 1
    return kept


def _reserve(qfd: int) -> str:
    """A fresh name in the quarantine folder, created exclusively so a rename onto it replaces nothing else."""
    while True:
        name = secrets.token_hex(8)
        try:
            os.close(
                os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=qfd)
            )
        except FileExistsError:  # trw-fail-silent-allow: taken; draw another name
            continue
        return name


def _missing_by_fd(sfd: int, project: Path, prefix: str = "", *, seen: Seen | None = None) -> list[str]:
    """The snapshot's files (read through *sfd*) whose bytes the project does not hold at the same path.

    Only regular files are read, opened non-blocking and fstat-checked, so a FIFO never stalls the check; any
    other kind, on either side, counts as missing from the project (E2E-INC-143 KI2). The identity added to *seen*
    (the only entries :func:`delete_verified` may remove) is the one of the descriptor whose bytes were compared,
    never a separate stat of the name (luna blocker, E2E-INC-143 r3).
    """
    seen = set() if seen is None else seen
    missing: list[str] = []
    for entry in sorted(os.scandir(sfd), key=lambda e: e.name):
        rel = f"{prefix}{entry.name}"
        if entry.is_dir(follow_symlinks=False):
            dfd = os.open(entry.name, _DIR_FLAGS, dir_fd=sfd)
            try:
                seen.add(_ident(os.fstat(dfd)))
                missing += _missing_by_fd(dfd, project, f"{rel}/", seen=seen)
            finally:
                os.close(dfd)
            continue
        if not prefix and entry.name in (_OWNER, _LOCK):  # TRW's own; nothing to compare
            seen.add(_ident(os.stat(entry.name, dir_fd=sfd, follow_symlinks=False)))
            continue
        here = project / rel
        got = _link_target(entry.name, sfd) if entry.is_symlink() else _read_regular(entry.name, sfd)
        if entry.is_symlink():
            same = got is not None and here.is_symlink() and os.readlink(here) == got[0]
        else:
            theirs = _read_regular(str(here), None)
            same = got is not None and theirs is not None and got[0] == theirs[0]
        if same and got is not None:
            seen.add(got[1])
        else:
            missing.append(rel)
    return missing


def _link_target(name: str, dir_fd: int) -> tuple[str, tuple[int, int]] | None:
    """A symlink's target and identity, only if the same link was there before and after the read."""
    try:
        before = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        target = os.readlink(name, dir_fd=dir_fd)
        after = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:  # trw-fail-silent-allow: unreadable counts as not the same link
        return None
    return (target, _ident(after)) if stat.S_ISLNK(after.st_mode) and _ident(before) == _ident(after) else None


def _read_regular(name: str, dir_fd: int | None) -> tuple[bytes, tuple[int, int]] | None:
    """A regular file's bytes and the identity of the descriptor they were read from, without following a link or
    blocking on a FIFO; None for anything else."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(name, flags, dir_fd=dir_fd)
    except OSError:  # trw-fail-silent-allow: absent or unreadable counts as not the same bytes
        return None
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            return None
        return handle.read(), _ident(info)


def _walk(sfd: int, folders: list[str]) -> int:
    fd = sfd
    for name in folders:
        nxt = os.open(name, _DIR_FLAGS, dir_fd=fd)
        if fd != sfd:
            os.close(fd)
        fd = nxt
    return fd
