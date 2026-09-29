"""Tree variant of ``remove_if_hash`` for stale skill/agent/command artifacts.

Belongs to the ``_safe_remove.py`` facade, which re-exports ``remove_tree_if_hash``. Split out of
``_trash.py`` to keep both under the 350 effective-LOC gate.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from collections.abc import Callable
from pathlib import Path

from ._trash import _CHUNK, _HASH_CAP, remove_if_hash
from ._utils import printable


def remove_tree_if_hash(artifact: Path, root: Path, allowed: Callable[[Path], set[str]]) -> list[str]:
    """Tree variant of :func:`remove_if_hash` for a stale skill/agent/command file or directory.

    Every regular file under *artifact* whose bytes hash to one of ``allowed(file)`` goes through
    :func:`remove_if_hash` (captured into ``.trw/trash`` and re-verified there, never unlinked). A file that
    is unlisted, changed, not regular, or unreadable is kept and named. Directories are then only ``rmdir``ed,
    deepest first, so one kept file (or a file created after the listing) keeps its directory. Returns
    why anything was kept; an empty list means *artifact* is gone.
    """
    kept: list[str] = []

    def shown(path: Path) -> str:
        try:
            return printable(path.relative_to(root).as_posix())
        except ValueError:  # outside root: remove_if_hash will refuse it; never abort the sweep
            return printable(str(path))

    try:
        is_tree = artifact.is_dir() and not artifact.is_symlink()
        entries = sorted(artifact.rglob("*")) if is_tree else [artifact]
    except OSError as exc:  # an inspection failure keeps the artifact and reports it; never abort the update
        return [f"{shown(artifact)} (could not inspect: {exc})"]
    for entry in entries:
        rel = shown(entry)
        try:
            if is_tree and entry.is_dir() and not entry.is_symlink():
                continue
            digest, why_not = _bounded_sha256(entry)
        except OSError as exc:
            kept.append(f"{rel} (unreadable: {exc})")
            continue
        if digest is None:
            kept.append(f"{rel} ({why_not})")
            continue
        if digest not in allowed(entry):
            kept.append(f"{rel} (not TRW's unchanged bytes)")
            continue
        outcome = remove_if_hash(entry, root, digest, key=rel)
        if outcome.status not in ("removed", "absent"):
            kept.append(f"{rel} ({outcome.reason})")
    if is_tree:
        try:
            dirs = [d for d in artifact.rglob("*") if d.is_dir() and not d.is_symlink()]
        except OSError:  # trw-fail-silent-allow: cannot list, so rmdir only the top; a non-empty one stays
            dirs = []
        for directory in [*sorted(dirs, key=lambda d: len(d.parts), reverse=True), artifact]:
            try:
                os.rmdir(directory)
            except OSError:  # trw-fail-silent-allow: not empty means something was kept or added; it stays
                pass
        if os.path.lexists(artifact) and not kept:
            kept.append(f"{shown(artifact)} (not empty after removal)")
    return kept


def _bounded_sha256(path: Path) -> tuple[str | None, str]:
    """``(sha256, "")`` of a regular file read through an O_NOFOLLOW|O_NONBLOCK fd, or ``(None, reason)`` when it is
    not a regular file (symlink, FIFO, device, directory) or is larger than the capture's size cap. A FIFO never
    blocks the sweep."""
    not_regular = (None, "not a regular file")
    too_large = (None, f"larger than the {_HASH_CAP // (1024 * 1024)} MiB cap")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):  # a symlink at the name: not TRW's regular file
            return not_regular
        raise
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return not_regular
        if info.st_size > _HASH_CAP:
            return too_large
        digest = hashlib.sha256()
        total = 0
        while chunk := os.read(fd, _CHUNK):
            total += len(chunk)
            if total > _HASH_CAP:  # grew past the cap while being read
                return too_large
            digest.update(chunk)
        return digest.hexdigest(), ""
    finally:
        os.close(fd)
