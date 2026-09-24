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
"""

from __future__ import annotations

import shutil
from pathlib import Path


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
    if path.is_symlink():
        return "refused: path is a symlink"
    current = root
    for part in rel_parts[:-1]:
        current = current / part
        if current.is_symlink():
            return f"refused: parent {current} is a symlink"
    try:
        resolved = path.resolve()
    except OSError as exc:
        return f"path could not be resolved: {exc}"
    try:
        resolved.relative_to(root_real)
    except ValueError:
        return "refused: path resolves outside root"
    return None


def safe_remove(path: Path, root: Path) -> str | None:
    """Remove *path* (file or dir) under *root* when :func:`path_refusal` allows it.

    Returns ``None`` on success, else the refusal reason or the ``OSError``
    text. Directory removal uses ``shutil.rmtree``, which does not follow
    symlinked subdirectories on POSIX (a nested symlinked dir is unlinked, not
    descended into), so a symlink planted *inside* an otherwise-safe directory
    cannot smuggle an outside deletion through this call either.
    """
    refusal = path_refusal(path, root)
    if refusal:
        return refusal
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as exc:
        return f"error removing {path}: {exc}"
    return None
