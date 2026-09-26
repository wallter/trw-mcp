"""Read a live SQLite file without ever closing a descriptor a lock depends on (C15 lock-drop class).

POSIX fcntl locks belong to a (process, inode) pair: closing ANY descriptor on
a database drops every lock this process holds on it, a live connection's
``BEGIN IMMEDIATE`` or ``locking_mode=EXCLUSIVE`` hold included
(sqlite.org/howtocorrupt.html 2.2). Opening never drops a lock, so reads go
through one descriptor per inode, opened on first use. A held descriptor also
keeps its inode allocated, so no replacement file can take over its
``(st_dev, st_ino)`` key while it is pinned. Windows ``LockFileEx`` locks are
per handle, so a plain open/read/close is safe there.

This module is the one owner of that descriptor map: code that reads raw bytes
of a SQLite file another connection in this process may hold goes through it.

**Retirement invariant.** A descriptor is closed only when the *path* it was
opened through now resolves to a *different* inode -- i.e. the file was
replaced or recreated, not written in place. A replaced inode can no longer be
the live store's lock target: this process only ever takes new locks against
the current path, which SQLite reopens by name, so nothing here will ever
``BEGIN IMMEDIATE``/``EXCLUSIVE``-lock the retired inode again. Any connection
that already held a lock on that inode released it (by design) when the file
was replaced out from under it, exactly as a bare ``close()`` would -- the
difference is this module never *causes* that release itself. Same-inode
rewrites (e.g. an in-place WAL header migration) never retire: the cached
descriptor keeps tracking the live file. The map is capped at
``_MAX_PINNED_FDS`` distinct *live* descriptors; a churn pattern that keeps
replacing one path's file retires the old entry on the next read of that same
path, so steady-state usage holds at most one descriptor per distinct
database file this process reads raw -- in practice one delivery journal per
project an MCP server serves, and the one mailbox a ``formation
comms-upgrade``/``comms-rollback`` process touches. Hitting the cap raises
:class:`PinnedReadCapacityExceeded` (an ``OSError``) instead of letting the
process run out of descriptors (EMFILE) for unrelated work too.

**Concurrency.** ``_fds_lock`` is held across the read itself (``os.pread``),
not just across retire/open, in ``read_at`` and ``copy_to``. Retiring a
descriptor while another thread is mid-``pread`` on it would either fail that
read with EBADF or, worse, hand it bytes from an unrelated file the OS reused
that fd number for. Serializing keeps a retire and a read on the same fd
mutually exclusive at the cost of a lock held for a few microseconds around a
tiny header read -- immeasurable next to the corruption/EBADF it prevents.
"""

from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path

_CHUNK = 1 << 20
_MAX_PINNED_FDS = 64

_fds: dict[tuple[int, int], int] = {}
_path_inode: dict[Path, tuple[int, int]] = {}
_fds_lock = threading.Lock()


class PinnedReadCapacityExceeded(OSError):
    """Raised instead of risking EMFILE when the pinned-fd cache is at its cap."""


def _retire_locked(path: Path, current: tuple[int, int]) -> None:
    """Close *path*'s previously-pinned descriptor if its inode has changed.

    Must be called with ``_fds_lock`` held. See the module docstring's
    retirement invariant for why this close never drops a live lock.
    """
    previous = _path_inode.get(path)
    if previous is not None and previous != current:
        stale_fd = _fds.pop(previous, None)
        if stale_fd is not None:
            os.close(stale_fd)


def _pinned_fd_locked(path: Path) -> int:
    """Return *path*'s pinned descriptor. Caller must already hold ``_fds_lock``.

    Holding the lock across the read that follows (see ``read_at``/``copy_to``)
    is what keeps a concurrent retire from ever closing an fd another thread is
    mid-``os.pread`` on -- header reads are tiny, so serializing them costs
    nothing measurable, and correctness beats the marginal concurrency here.
    """
    st = os.stat(path)
    key = (st.st_dev, st.st_ino)
    _retire_locked(path, key)
    fd = _fds.get(key)
    if fd is None:
        if len(_fds) >= _MAX_PINNED_FDS:
            raise PinnedReadCapacityExceeded(
                f"pinned-read fd cache is at its {_MAX_PINNED_FDS}-entry cap; "
                f"refusing to open another descriptor for {path}"
            )
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        opened = os.fstat(fd)
        # Keyed by the inode actually opened. If the path was replaced between
        # the stat and the open and that inode is already pinned, the extra
        # descriptor stays open unreferenced: a leak, never a close.
        fd = _fds.setdefault((opened.st_dev, opened.st_ino), fd)
    _path_inode[path] = key
    return fd


def read_at(path: Path, length: int, offset: int = 0) -> bytes:
    """Up to *length* bytes of *path* from *offset*; short only at end of file."""
    if os.name == "nt":
        with path.open("rb") as handle:
            handle.seek(offset)
            return handle.read(length)
    with _fds_lock:
        fd = _pinned_fd_locked(path)
        return os.pread(fd, length, offset)


def copy_to(path: Path, destination: Path) -> None:
    """Write *path*'s bytes to *destination* (created or truncated), like ``shutil.copyfile``."""
    if os.name == "nt":
        shutil.copyfile(path, destination)
        return
    with _fds_lock:
        fd = _pinned_fd_locked(path)
        with destination.open("wb") as out:
            offset = 0
            while chunk := os.pread(fd, _CHUNK, offset):
                out.write(chunk)
                offset += len(chunk)


__all__ = ["PinnedReadCapacityExceeded", "copy_to", "read_at"]
