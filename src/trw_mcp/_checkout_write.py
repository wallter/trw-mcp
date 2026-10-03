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
import hashlib
import os
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path, PurePath

from trw_memory.safe_fs import UnsafeWriteError, append_beneath, write_beneath

from trw_mcp._locking import _lock_ex, _lock_un

__all__ = [
    "UnsafeWriteError",
    "append_checkout_file",
    "record_run_write",
    "recording_writes",
    "write_checkout_file",
    "written_this_run",
]

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
    payload = _encode(data)
    if mode is not None:
        write_beneath(root, rel, payload, mode=mode)
    else:
        kept = _mode_to_keep(root, rel)
        write_beneath(root, rel, payload, mode=_NEW_FILE_MODE if kept is None else kept, exact_mode=kept is not None)
    ledger = _RUN_WRITES.get()
    if ledger is not None:
        ledger[os.path.realpath(path)] = hashlib.sha256(payload).hexdigest()


#: Bytes this run wrote, by absolute path. Only the update transaction opens one (:func:`recording_writes`):
#: its rollback and dirty-file restore may delete a file only on POSITIVE PROOF it is this run's own write or
#: the snapshot's own bytes; anything else is someone else's and goes to .trw/trash (FB-01-KI1-RACE r4).
_RUN_WRITES: ContextVar[dict[str, str] | None] = ContextVar("trw_run_writes", default=None)


@contextmanager
def recording_writes() -> Iterator[dict[str, str]]:
    """Record every :func:`write_checkout_file` in this context (thread- and task-local)."""
    ledger: dict[str, str] = {}
    token = _RUN_WRITES.set(ledger)
    try:
        yield ledger
    finally:
        _RUN_WRITES.reset(token)


def record_run_write(path: Path, data: bytes) -> None:
    """Record that this run published *data* at *path* (no-op unless a ledger is open).

    The writer passes the bytes it WROTE: re-reading the path would trust bytes a concurrent writer put there
    in the meantime, and the restore would then delete them as this run's own (codex r4 KI1a).
    """
    ledger = _RUN_WRITES.get()
    if ledger is not None:
        ledger[os.path.realpath(path)] = hashlib.sha256(data).hexdigest()


def written_this_run(path: Path) -> str | None:
    """The sha256 this run last wrote to *path*, or None when it wrote nothing there (or no ledger is open)."""
    ledger = _RUN_WRITES.get()
    return None if ledger is None else ledger.get(os.path.realpath(path))


def append_checkout_file(root: Path, path: Path, data: str | bytes, *, lock: bool = False) -> None:
    """Append *data* to *path*, which must lie under *root*, creating it when absent; never follows a symlink.

    With *lock* the append holds an exclusive ``flock`` for a log whose readers take a shared one
    (``FileStateReader.read_jsonl``). The lock is taken on the very no-follow fd the bytes go through, never on
    a second open of the path, so a link swapped in between cannot make it lock one file and write another.
    The parent directory must exist (callers ``mkdir`` it first). Windows has no ``dir_fd``: unlocked append.
    """
    rel = path.relative_to(root)
    payload = _encode(data)
    if not lock or os.open not in os.supports_dir_fd:
        append_beneath(root, rel, payload, mode=_NEW_FILE_MODE)
        return
    # Imported here, not at module level: a trw-memory older than this symbol must not break every importer.
    from trw_memory.safe_fs import open_parent_beneath

    parent_fd, leaf = open_parent_beneath(root, rel)
    try:
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        try:
            fd = os.open(leaf, flags, _NEW_FILE_MODE, dir_fd=parent_fd)
        except OSError as exc:
            try:  # a link removed again before this check must not replace the refusal with a stat error
                linked = exc.errno == errno.ELOOP or stat.S_ISLNK(
                    os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False).st_mode
                )
            except OSError:
                linked = False
            if linked:
                raise UnsafeWriteError(
                    f"refusing to write {path}: symlink_leaf", path=str(path), reason="symlink_leaf"
                ) from exc
            raise
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise UnsafeWriteError(
                    f"refusing to write {path}: leaf_not_regular_file", path=str(path), reason="leaf_not_regular_file"
                )
            _lock_ex(fd)
            try:
                view = memoryview(payload)
                while view:
                    view = view[os.write(fd, view) :]
            finally:
                _lock_un(fd)
        finally:
            os.close(fd)
    finally:
        os.close(parent_fd)


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
    if not rel.parts:
        return None  # the root itself names no file: the write then refuses it (escapes_root)
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
