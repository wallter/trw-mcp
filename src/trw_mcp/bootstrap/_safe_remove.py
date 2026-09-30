"""Symlink-safe delete primitive shared by every uninstall deletion path.

PRD-INFRA-192 FR09 P0. Before this module, ``_run_uninstall`` resolved a
recorded surface path with ``Path.resolve()`` *before* deciding what to do
with it, then ``shutil.rmtree``/``unlink``ed the RESOLVED path. If ``.trw``
(or any client surface, or a parent path component) was a symlink to a
directory outside the project, uninstall deleted the SYMLINK TARGET's
contents — potentially outside the project entirely.

One rule governs every rmtree/unlink call site in the uninstall path
(``server/_subcommands_lifecycle.py``, ``bootstrap/_uninstall_manifest.py``):
TRW never removes a byte it cannot prove it wrote and that is unchanged. A
symlink is never TRW's recorded content — TRW only ever wrote the plain
files/dirs it tracks — so any symlink in the path to what would be deleted
is refused outright, never dereferenced and deleted through.

Known limits (both fail safe or are documented, neither is fixed here):

- Case-insensitive filesystems fail closed: a target spelled with different
  case than *root* is refused as "not under root" rather than matched.
- The final component is re-``lstat``ed at the act, but a swap of an
  INTERMEDIATE parent directory between the check and the delete is not
  fd-anchored (no ``dir_fd``-relative removal), so that window remains.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path
from typing import Literal


def _lstat_mode(path: Path) -> int | None:
    """``lstat`` mode of *path*, or ``None`` only when it is genuinely absent.

    Any other ``OSError`` (EACCES on an unreadable parent, ...) propagates:
    ``Path.is_symlink``/``exists`` swallow or re-raise inconsistently across
    Python versions, and "could not stat" must mean preserve, never "absent".
    """
    mode: int | None = None
    try:
        mode = os.lstat(path).st_mode
    except (FileNotFoundError, NotADirectoryError):
        mode = None  # absent: the only OSError that means "nothing there"
    return mode


def _is_symlink(path: Path) -> bool:
    mode = _lstat_mode(path)
    return mode is not None and stat.S_ISLNK(mode)


def path_refusal(path: Path, root: Path) -> str | None:
    """Return why removing *path* under *root* is unsafe, or ``None`` if safe.

    Refuses when:
    - *path* itself is a symlink (``lstat``/``is_symlink``, never dereferenced
      first). For a symlinked directory this obviously must not be followed
      into and rmtree'd. For a symlinked FILE, unlinking just the link would
      not remove any bytes outside *root* — but the link itself is not what
      TRW recorded (TRW wrote a plain file at that path, not a redirect), so
      it is refused too rather than silently treated as "close enough".
    - any path component between *root* and *path* is itself a symlink — an
      intermediate directory redirect would otherwise carry every check below
      through a link even though *path*'s own final component is a plain name.
    - *path* is *root* itself, or resolves to it (or to an ancestor of it).
    - ``path.resolve()`` does not sit inside ``root.resolve()``. This is a
      belt-and-braces check once the two rules above hold (a plain path with
      no symlink component cannot normally escape *root*, short of ``..``
      segments the caller should not be constructing), not the primary defense.
    """
    try:
        root_real = root.resolve()
    except OSError as exc:
        return f"root could not be resolved: {exc}"
    try:
        rel_parts = path.relative_to(root).parts
    except ValueError:
        return "path is not under root"
    if not rel_parts:
        return "refused: path is the root"
    try:
        if _is_symlink(path):
            return "refused: path is a symlink"
        current = root
        for part in rel_parts[:-1]:
            current = current / part
            if _is_symlink(current):
                return f"refused: parent {current} is a symlink"
    except OSError as exc:
        return f"refused: could not inspect path ({type(exc).__name__}): {exc}"
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError) as exc:  # RuntimeError: symlink loop on py3.11/3.12
        return f"path could not be resolved: {exc}"
    try:
        resolved.relative_to(root_real)
    except ValueError:
        return "refused: path resolves outside root"
    if resolved == root_real:
        return "refused: path resolves to the root"
    return None


def safe_remove(path: Path, root: Path, *, expect: Literal["file", "dir", "any"] = "any") -> str | None:
    """Remove *path* (file or dir) under *root* when :func:`path_refusal` allows it.

    Returns ``None`` on success or when the target is already absent, else the refusal reason or the ``OSError``
    text. Directory removal uses ``shutil.rmtree``, which does not follow
    symlinked subdirectories on POSIX (a nested symlinked dir is unlinked, not
    descended into), so a symlink planted *inside* an otherwise-safe directory
    cannot smuggle an outside deletion through this call either.

    *expect* pins the kind the caller planned to delete. It is checked against the act-time ``lstat``: a
    file site must never escalate to a recursive delete because the file was swapped for a directory, and a
    directory site must not unlink a file swapped in. A mismatch is refused and nothing is removed.
    """
    refusal = path_refusal(path, root)
    if refusal:
        return refusal
    try:
        mode = _lstat_mode(path)
        if mode is None:
            # Already gone (or a parent is not a directory): the goal is met. Acting anyway
            # could delete a file a concurrent writer just created, which is not ours.
            return None
        if stat.S_ISLNK(mode):
            # Re-checked at the act: a swap to a symlink after path_refusal must not be followed.
            return "refused: path is a symlink"
        is_dir = stat.S_ISDIR(mode)
        if expect == "file" and is_dir:
            return "refused: expected a file, found a directory"
        if expect == "dir" and not is_dir:
            return "refused: expected a directory, found a file"
        if is_dir:
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as exc:
        return f"error removing {path}: {exc}"
    return None


# The trash-backed half lives in ``_trash.py``; re-exported so existing imports keep working.
from ._trash import (  # noqa: E402
    _SHA256_RE as _SHA256_RE,
)
from ._trash import (  # noqa: E402
    TRASH_DIR_NAME as TRASH_DIR_NAME,
)
from ._trash import (  # noqa: E402
    Removal as Removal,
)
from ._trash import (  # noqa: E402
    _capable as _capable,
)
from ._trash import (  # noqa: E402
    move_captures_to_os_trash as move_captures_to_os_trash,
)
from ._trash import (  # noqa: E402
    remove_if_hash as remove_if_hash,
)
from ._trash_purge import (  # noqa: E402
    delete_proven_unchanged_captures as delete_proven_unchanged_captures,
)
from ._trash import (  # noqa: E402
    trash_dir as trash_dir,
)
from ._trash_tree import (  # noqa: E402
    remove_tree_if_hash as remove_tree_if_hash,
)
