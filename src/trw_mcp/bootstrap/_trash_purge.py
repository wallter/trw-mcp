"""Uninstall's non-macOS counterpart of ``move_captures_to_os_trash``: delete only provably unchanged captures.

Belongs to the ``_safe_remove.py`` facade, which re-exports the public name here. Split out of ``_trash.py`` to keep
both modules under the 350 effective-LOC gate.

Off macOS there is no system Trash to rename into, so an explicit uninstall would otherwise leave ``.trw/trash`` behind
on every Linux and Windows host. A capture is deleted only when everything below holds; anything else stays in
``.trw/trash`` and is reported with the reason (HB-2: unreadable, unproven or unexpected means preserve).
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from trw_mcp.bootstrap._trash import (
    _CAPTURE_NAME_RE,
    _SHA256_RE,
    _UNSUPPORTED,
    TRASH_DIR_NAME,
    _open_dir,
    _sha256_stable,
    _walk,
    trash_dir,
)

_META_CAP = 64 * 1024


def _read_meta(cfd: int) -> dict[str, object] | None:
    """``meta.json`` of a capture folder, or ``None`` unless it is a regular file holding a JSON object."""
    fd = os.open("meta.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=cfd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        raw = os.read(fd, _META_CAP + 1)
    finally:
        os.close(fd)
    if len(raw) > _META_CAP:
        return None
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else None


def _purge_one(tfd: int, folder: str) -> str | None:
    """Delete one proven capture; return why it was kept, or ``None`` when it was deleted."""
    cfd = _open_dir(folder, tfd)
    try:
        meta = _read_meta(cfd)
        recorded = meta.get("sha256") if meta else None
        if not isinstance(recorded, str) or _SHA256_RE.fullmatch(recorded) is None:
            return "no recorded hash to prove it unchanged"
        dfd = os.open("data", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=cfd)
        try:
            info = os.fstat(dfd)
            if not stat.S_ISREG(info.st_mode):
                return "not a regular file"
            if info.st_nlink != 1:
                return "another name refers to the same bytes"
            if not _sha256_stable(dfd, recorded, cfd):
                return "changed since TRW wrote it"
            # Proven a moment ago. Re-check the inode right before the unlink so the window in which a writer that
            # holds an fd on it could add bytes we then drop is only the gap between this fstat and the unlink;
            # any sign of a write, a new name or a swapped name keeps the capture.
            again = os.fstat(dfd)
            named = os.stat("data", dir_fd=cfd, follow_symlinks=False)
            if (again.st_size, again.st_mtime_ns, again.st_ctime_ns, again.st_nlink) != (
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
                1,
            ) or (named.st_dev, named.st_ino) != (again.st_dev, again.st_ino):
                return "changed while being checked"
            # trw:intentional lead ruling 2026-09-29: off macOS there is no Trash to rename into, so the proven
            # capture is unlinked. A late write through an fd held from before the capture inside that last gap
            # is unrecoverable; the proof above (hash, sole name, unchanged stat twice) is the mitigation.
            os.unlink("data", dir_fd=cfd)
        finally:
            os.close(dfd)
        os.unlink("meta.json", dir_fd=cfd)
    finally:
        os.close(cfd)
    os.rmdir(folder, dir_fd=tfd)
    return None


def delete_proven_unchanged_captures(root: Path, data_paths: list[Path]) -> tuple[int, list[tuple[Path, str]]]:
    """Delete capture folders whose ``meta.json`` hash still matches their ``data``; keep the rest.

    Each *data_path* must be ``<root>/.trw/trash/<stamp>-<hex32>/data``. Returns the number of captures deleted and
    ``(data_path, why)`` for every capture left in ``.trw/trash``.
    """
    kept: list[tuple[Path, str]] = []
    if _UNSUPPORTED:
        return 0, [(p, _UNSUPPORTED) for p in data_paths]
    deleted = 0
    fds: list[int] = []
    try:
        try:
            root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            fds.append(root_fd)
            tfd = _walk(root_fd, (".trw", TRASH_DIR_NAME), create=False)
            fds.append(tfd)
        except OSError as exc:
            return 0, [(p, f"cannot open .trw/trash ({type(exc).__name__})") for p in data_paths]
        for data_path in data_paths:
            folder = data_path.parent.name
            if (
                data_path.name != "data"
                or data_path.parent.parent != trash_dir(root)
                or not _CAPTURE_NAME_RE.fullmatch(folder)
            ):
                kept.append((data_path, "not a TRW capture folder"))
                continue
            try:
                why = _purge_one(tfd, folder)
            except (
                OSError,
                ValueError,
                RecursionError,
            ) as exc:  # unreadable, malformed or absurdly nested: preserve, and say why
                why = f"could not prove it unchanged ({type(exc).__name__})"
            if why is None:
                deleted += 1
            else:
                kept.append((data_path, why))
    finally:
        for fd in reversed(fds):
            os.close(fd)
    return deleted, kept
