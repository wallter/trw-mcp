"""trw-mcp's one way to write a file inside a project checkout (PRD-CORE-337 FR06/FR07).

A checkout is not trusted content: a hostile branch or template can plant a symlink at
``.cursor/hooks.json``, or make ``.codex/`` itself a symlink, and a plain ``Path.write_text`` then
writes wherever the link points. The bootstrap generators and the channel writers therefore write
through :mod:`trw_memory.safe_fs`, which walks every component below *root* without following a
symlink. This module adapts that primitive to the call shape those sites had, so each site changes
one call and its output is unchanged for a checkout with no planted symlink:

* ``str`` data is encoded as ``Path.write_text(text, encoding="utf-8")`` encodes it (UTF-8, each
  ``"\\n"`` written as ``os.linesep``); ``bytes`` are written as given.
* :func:`write_checkout_file` replaces the whole file (temp file plus ``os.replace``). An existing
  regular file keeps its permission bits EXACTLY, as an in-place ``write_text`` kept them (the umask is
  not applied to them a second time); a new file is created at ``0o666``, which the umask narrows, as
  ``open()`` does. If the existing file's bits cannot be read for any reason other than it being absent
  or behind a symlinked component (which the write then refuses), the error is raised and nothing is
  written: a failed lookup never falls back to the default mode, which could widen a tightened file.
* :func:`append_checkout_file` appends, creating the file when absent.
* Both create missing parent directories, which is what the ``mkdir(parents=True)`` each site used to
  call first did.

What changes is the refusal: a symlink at *root* or at any component below it raises
:class:`~trw_memory.safe_fs.UnsafeWriteError` and nothing is written. It is deliberately not an
``OSError``; a site that reports write failures into its result catches it beside ``OSError``.

*root* is the directory the caller trusts, normally the project root the command was pointed at;
components below it are walked no-follow. A site that only knows a leaf path passes that path's
parent, which protects the leaf alone -- each such site says so where it calls.
"""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Callable
from pathlib import Path, PurePath

from trw_memory.safe_fs import UnsafeWriteError, append_beneath, write_beneath

__all__ = ["UnsafeWriteError", "append_checkout_file", "write_checkout_file"]

#: What ``open()`` (and so ``Path.write_text``) passes when it creates a file; the umask narrows it.
_NEW_FILE_MODE = 0o666
_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def write_checkout_file(root: Path, path: Path, data: str | bytes, *, mode: int | None = None) -> None:
    """Replace *path*, which must lie under *root*, with *data*, refusing any symlinked component.

    *mode* publishes the file at those permission bits (narrowed by the umask) instead of keeping the
    replaced file's -- for a copy that must carry its SOURCE's mode, set at creation with no later chmod.
    Raises ``UnsafeWriteError`` on a refusal, ``ValueError`` when *path* is not under *root*, and the
    ``OSError`` the operating system raised when the write itself fails.
    """
    rel = path.relative_to(root)
    if mode is not None:
        write_beneath(root, rel, _encode(data), mode=mode)
        return
    kept = _mode_to_keep(root, rel)
    write_beneath(root, rel, _encode(data), mode=_NEW_FILE_MODE if kept is None else kept, exact_mode=kept is not None)


def append_checkout_file(root: Path, path: Path, data: str | bytes) -> None:
    """Append *data* to *path*, which must lie under *root*, creating it when absent; never follows a symlink."""
    append_beneath(root, path.relative_to(root), _encode(data), mode=_NEW_FILE_MODE)


def _encode(data: str | bytes) -> bytes:
    if isinstance(data, bytes):
        return data
    return (data if os.linesep == "\n" else data.replace("\n", os.linesep)).encode("utf-8")


def _mode_to_keep(root: Path, rel: PurePath) -> int | None:
    """The permission bits of the regular file at ``root/rel``; ``None`` when there is none to keep.

    The bits never come from a file a symlink points at: every directory below *root* is opened
    ``O_NOFOLLOW`` relative to the one before, and the leaf is stat-ed through its parent's fd with
    ``follow_symlinks=False`` (a by-name ``lstat`` would follow a symlinked PARENT and could copy an
    outside file's mode onto the published one). A link or an absent leaf answers ``None`` (a new
    file), and the write itself then refuses the link; any other failure is raised (fail closed).
    """
    if os.stat not in os.supports_dir_fd:  # Windows: no dir_fd; safe_fs is best effort there too
        return _regular_mode(lambda: os.lstat(root / rel))
    fds: list[int] = []

    def _stat_leaf() -> os.stat_result:
        fds.append(os.open(root, _DIR_FLAGS))
        for name in rel.parts[:-1]:
            fds.append(os.open(name, _DIR_FLAGS, dir_fd=fds[-1]))
        return os.stat(rel.parts[-1], dir_fd=fds[-1], follow_symlinks=False)

    try:
        return _regular_mode(_stat_leaf)
    finally:
        for fd in fds:
            os.close(fd)


#: Lookup failures that mean "no file of ours to keep": absent, or a symlinked component the write refuses.
_NO_FILE_ERRNOS = frozenset({errno.ENOENT, errno.ELOOP, errno.ENOTDIR})


def _regular_mode(stat_leaf: Callable[[], os.stat_result]) -> int | None:
    try:
        st = stat_leaf()
    except OSError as exc:
        if (
            exc.errno in _NO_FILE_ERRNOS
        ):  # trw-fail-silent-allow: absent, or a symlinked component the write then refuses
            return None
        raise  # EACCES and the like: never guess a mode that could widen a tightened file
    return stat.S_IMODE(st.st_mode) & 0o777 if stat.S_ISREG(st.st_mode) else None
