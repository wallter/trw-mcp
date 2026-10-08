"""Retire a whole skill directory by an atomic rename-aside (the directory-scoped half of :mod:`._retire`)."""

from __future__ import annotations

import hashlib
import os
import secrets
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from ._artifact_names import RETIRING_PREFIX
from ._retire import Retirement, _judge
from ._trash import _CHUNK, _HASH_CAP, _UNSUPPORTED, _walk
from ._utils import printable

__all__ = ["retire_whole"]


def _fd_sha(name: str, dir_fd: int) -> str:
    """sha256 of the regular file *name* in *dir_fd* (O_NOFOLLOW, never blocking), else a marker that matches nothing."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    except OSError:  # trw-fail-silent-allow: unreadable or a symlink; the marker makes the verification fail closed
        return "unreadable"
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            return "special"
        digest = hashlib.sha256()
        total = 0
        while chunk := os.read(fd, _CHUNK):
            total += len(chunk)
            if total > _HASH_CAP:
                return "too large"
            digest.update(chunk)
        after = os.fstat(fd)
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            return "changing"
        return digest.hexdigest()
    finally:
        os.close(fd)


def _device(st: os.stat_result) -> int:
    """The device a stat result lives on (one seam, so a mount inside a skill can be simulated)."""
    return st.st_dev


def _scan(dir_fd: int, dev: int, prefix: str = "") -> dict[str, str]:
    """Every entry under the directory *dir_fd* without following a symlink: ``rel -> sha256`` for a file,
    ``rel/ -> ""`` for a directory, a marker for a symlink, special file or an entry on another device *dev* (a mount
    inside the skill, which is never walked)."""
    found: dict[str, str] = {}
    with os.scandir(dir_fd) as it:
        entries = list(it)
    for entry in entries:
        rel = prefix + entry.name
        if entry.is_symlink():
            found[rel] = "symlink"
        elif _device(entry.stat(follow_symlinks=False)) != dev:
            found[rel] = "other device"
        elif entry.is_dir(follow_symlinks=False):
            found[rel + "/"] = ""
            child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
            try:
                found.update(_scan(child, dev, rel + "/"))
            finally:
                os.close(child)
        elif entry.is_file(follow_symlinks=False):
            found[rel] = _fd_sha(entry.name, dir_fd)
        else:
            found[rel] = "special"
    return found


def _difference(expected: dict[str, str], found: dict[str, str]) -> str:
    """A short, escaped account of how *found* differs from *expected*."""
    if mounted := sorted(k for k, v in found.items() if v == "other device"):
        return f"it crosses a mount at {printable(mounted[0])}"
    added = sorted(set(found) - set(expected))
    missing = sorted(set(expected) - set(found))
    changed = sorted(k for k in set(expected) & set(found) if expected[k] != found[k])
    parts = [
        f"{label} {', '.join(printable(n) for n in names[:3])}"
        for label, names in (("added", added), ("missing", missing), ("changed", changed))
        if names
    ]
    return "; ".join(parts) or "differs"


def _identity(st: os.stat_result) -> tuple[int, int, int, int, int]:
    """What a concurrent change would move; access time is left out because hashing the file can update it."""
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _remove_verified(dir_fd: int, found: dict[str, str], removed: list[str]) -> None:
    """Unlink only scanned files, rechecking bytes and identity; then rmdir deepest first.

    Never enumerate for deletion: a late entry makes rmdir fail and stops retirement.
    All parent components are opened without following symlinks.
    """
    files = [rel for rel in found if not rel.endswith("/")]
    directories = sorted((rel for rel in found if rel.endswith("/")), key=lambda rel: rel.count("/"), reverse=True)
    for rel in [*files, *directories]:
        parts = Path(rel).parts
        parent = _walk(dir_fd, parts[:-1], create=False)
        try:
            if _device(os.fstat(parent)) != _device(os.fstat(dir_fd)):
                raise OSError("directory moved to another device")
            if rel.endswith("/"):
                os.rmdir(parts[-1], dir_fd=parent)
                continue
            before = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            digest = _fd_sha(parts[-1], parent)
            after = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            if digest != found[rel] or _identity(before) != _identity(after):  # not st_atime: the hash read moves it
                raise OSError(f"file changed: {printable(rel)}")
            os.unlink(parts[-1], dir_fd=parent)
            removed.append(rel)
        finally:
            os.close(parent)


def retire_whole(
    artifact: Path,
    root: Path,
    entries: list[Path],
    allowed: Callable[[Path], set[str]],
    shipped_for: Callable[[Path], set[str]],
    shown: Callable[[Path], str],
) -> Retirement:
    """Prove the whole skill before retirement, preserving additions or changes during removal.

    The proof is decided first (every file recorded as TRW's or byte-equal to what TRW ships, else the directory is
    kept untouched). Then the directory is renamed to a unique sibling through the verified no-follow parent fd,
    re-verified there (same entries, same hashes), and only then removed with an fd-relative walk. Anything that
    differs renames the remaining directory back; if the name has been taken meanwhile it stays at the sibling
    name, which the notice states. A concurrent change can leave a partially retired skill, but only verified
    files are unlinked: new or changed entries stop removal and survive."""
    name = shown(artifact)

    def kept(why: str, *, dir_kept: bool = True, partial: bool = False) -> Retirement:
        removed_paths = [f"{name}/{rel}" for rel in removed] if partial else []
        removed_trw = [path for path in removed_paths if statuses.get(path.removeprefix(f"{name}/")) == "removed"]
        removed_git = [path for path in removed_paths if statuses.get(path.removeprefix(f"{name}/")) == "git"]
        if partial and removed_paths:
            why += f"; {len(removed_paths)} verified file(s) were already removed"
        return Retirement(removed_trw, removed_git, [(name, why)], frozenset({name}) if dir_kept else frozenset())

    expected: dict[str, str] = {}
    statuses: dict[str, Literal["removed", "git"]] = {}
    for entry in entries:
        rel = entry.relative_to(artifact).as_posix()
        if entry.is_dir() and not entry.is_symlink():
            expected[rel + "/"] = ""
            continue
        refused, digest, status = _judge(entry, root, allowed(entry), None, shipped_for(entry))
        if refused is not None:
            return kept(f"{rel}: {refused.why or 'vanished'}, so the skill was kept whole")
        expected[rel], statuses[rel] = digest, status
    if _UNSUPPORTED:
        return kept(f"{_UNSUPPORTED}, so the skill was kept whole")
    parts = artifact.relative_to(root).parts
    aside = f"{RETIRING_PREFIX}{parts[-1]}-{secrets.token_hex(8)}"
    aside_rel = "/".join((*parts[:-1], aside))
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    pfd = -1
    removed: list[str] = []
    try:
        try:
            pfd = _walk(root_fd, parts[:-1], create=False)  # O_NOFOLLOW on every component: a swapped parent is refused
            if not stat.S_ISDIR(os.stat(parts[-1], dir_fd=pfd, follow_symlinks=False).st_mode):
                return kept("it is not a plain directory, so it was kept")
            os.rename(parts[-1], aside, src_dir_fd=pfd, dst_dir_fd=pfd)
        except OSError as exc:
            return kept(f"could not be set aside ({exc.strerror or type(exc).__name__}), so it was kept as it was")
        removing = False
        try:
            dfd = os.open(aside, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
            try:
                found = _scan(dfd, _device(os.fstat(dfd)))
                problem = "" if found == expected else _difference(expected, found)
                if not problem:
                    removing = True
                    _remove_verified(dfd, found, removed)
                    current = os.stat(aside, dir_fd=pfd, follow_symlinks=False)
                    if not os.path.samestat(current, os.fstat(dfd)):
                        raise OSError("the aside directory changed")
                    os.rmdir(aside, dir_fd=pfd)
            finally:
                os.close(dfd)
        except OSError as exc:
            problem = (
                f"files were added or changed during retirement ({exc.strerror or str(exc)})"
                if removing
                else f"could not be verified ({exc.strerror or type(exc).__name__})"
            )
        if not problem:
            return Retirement(
                [f"{name}/{rel}" for rel in removed if statuses.get(rel) == "removed"],
                [f"{name}/{rel}" for rel in removed if statuses.get(rel) == "git"],
                [],
            )
        try:
            os.stat(parts[-1], dir_fd=pfd, follow_symlinks=False)
        except FileNotFoundError:
            try:
                os.rename(aside, parts[-1], src_dir_fd=pfd, dst_dir_fd=pfd)
            except OSError as exc:
                return kept(
                    f"kept at {printable(aside_rel)} (changed while it was removed: {problem}; it could not be renamed back: {exc.strerror or type(exc).__name__})",
                    dir_kept=False,
                    partial=removing,
                )
            if removing:
                return Retirement(
                    [f"{name}/{rel}" for rel in removed if statuses.get(rel) == "removed"],
                    [f"{name}/{rel}" for rel in removed if statuses.get(rel) == "git"],
                    [
                        (
                            name,
                            f"partly retired, then stopped: {problem}; files already verified and removed stay removed, "
                            f"and everything left is back at {name}",
                        )
                    ],
                    frozenset({name}),
                )
            return kept(f"changed while it was being removed ({problem}), so it was put back as it was")
        except OSError as exc:
            return kept(
                f"kept at {printable(aside_rel)} (changed while it was removed: {problem}; its name could not be checked: {exc.strerror or type(exc).__name__})",
                dir_kept=False,
                partial=removing,
            )
        return kept(
            f"kept at {printable(aside_rel)} (changed while it was removed: {problem}; the name was taken again, so move it back yourself)",
            dir_kept=False,
            partial=removing,
        )
    finally:
        if pfd >= 0:
            os.close(pfd)
        os.close(root_fd)


def scan_leftover(target_dir: Path, rel: Path) -> dict[str, str]:
    """``rel -> sha256`` for every entry of the leftover directory *rel* under *target_dir*, read through no-follow
    directory fds (the same scan retirement verifies with). Raises ``OSError`` when it cannot be read."""
    root_fd = os.open(target_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        dir_fd = _walk(root_fd, rel.parts, create=False)
        try:
            return _scan(dir_fd, _device(os.fstat(dir_fd)))
        finally:
            os.close(dir_fd)
    finally:
        os.close(root_fd)
