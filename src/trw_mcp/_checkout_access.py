"""One module owns every checkout-file open trw-mcp performs (PRD-CORE-316).

Two independently-maintained implementations answered the same question --
"can I read these bytes from a checkout-supplied or checkout-adjacent file
without following a symlink out of it, and without racing myself" -- before
this module existed: the code index's one-shot walk
(``code_index/discovery.py``'s ``_open_under``/``read_indexed_file``) and the
pinned-fd cache (the former ``_pinned_read.py``) used by the comms mailbox
header read and the delivery-journal header read. This module consolidates
both onto one shared O_NOFOLLOW component-open primitive (:func:`open_under`)
so a class of defect -- a global lock held across I/O, a mis-retired
descriptor, an un-bounded walk -- closes in one place instead of site by site
(PRD-CORE-316 Problem Statement).

**Pinned-fd cache: why it exists.** POSIX ``fcntl`` locks belong to a
(process, inode) pair: closing ANY descriptor on a database drops every lock
this process holds on it, a live connection's ``BEGIN IMMEDIATE`` or
``locking_mode=EXCLUSIVE`` hold included (sqlite.org/howtocorrupt.html 2.2).
Opening never drops a lock, so reads go through one descriptor per inode,
opened on first use. A held descriptor also keeps its inode allocated, so no
replacement file can take over its ``(st_dev, st_ino)`` key while it is
pinned. Windows ``LockFileEx`` locks are per handle, so a plain open/read/
close is safe there.

**Retirement invariant.** A path's descriptor is retired only when that
*path* now resolves to a *different* inode than last recorded for it -- i.e.
the file was replaced or recreated, not written in place. Retirement never
closes a descriptor a DIFFERENT tracked path still names through a hard link
(PRD-CORE-316 FR02(a)): each inode key tracks the set of paths currently
pointing at it, and the descriptor closes only when that set becomes empty.
A stat-then-open race that opens a second descriptor for an inode already
pinned closes the extra descriptor under the map lock instead of leaking it
outside the cap (FR02(b)). The map is capped at ``_MAX_PINNED_FDS`` distinct
*live* descriptors; hitting the cap raises
:class:`PinnedReadCapacityExceeded` (an ``OSError``) rather than evicting a
live descriptor -- UNLESS the path being read is the sole tracked owner of
its own previous inode, in which case retiring that association frees a slot
net-zero and the open is allowed (otherwise a replaced mailbox or journal
file would become permanently unrefreshable once the cache reached its cap).
**The "never evict a live descriptor another path still needs" policy is a
recorded decision (B71-08, ``docs/sprint-mcp7/PLAN-7.1.md:201``), owned by
the lead pending worker-2's measurement of real replacement rates;
PRD-CORE-316 preserves it unchanged (NFR04) for every descriptor still in
use and does not revisit the eviction policy itself here.**

**Dead-path sweep (E2E-INC-103).** Before that refusal stands, :func:`_evict_dead_paths` drops the
bookkeeping (and, once no tracked path names an inode, the descriptor) of paths whose ``lstat`` raises
``FileNotFoundError`` -- a deleted mailbox is not "a live descriptor another path still needs", and
without the sweep a long-lived server that touched 64 distinct files could never read another. Only
absence counts as dead: a path that still exists, even one now naming a DIFFERENT inode, is left to the
ordinary retirement rules, so a swap is never made easier by the sweep. The ``lstat`` calls run with no
lock held (a snapshot of ``_path_inode``, then a recheck under ``_map_lock`` that the path still maps to
the inode that was snapshotted); retirement reuses :func:`_retire_locked`/:func:`_close_stale`.

**Concurrency (PRD-CORE-316 FR01/NFR02).** Two locks, not one:

- ``_map_lock`` guards only the shared dicts' own mutations (insert/retire
  bookkeeping) -- ``os.stat``/``os.open``/``os.close`` calls, never a
  ``pread`` or a copy's write loop. It is held for at most a few syscalls.
- A per-``(dev, ino)`` lock, looked up or created under ``_map_lock``, is
  held across that inode's own ``os.pread``/``copy_to`` I/O. A stalled read
  or a large copy on one inode blocks only readers of that SAME inode; an
  unrelated inode's read proceeds concurrently. A stale descriptor is closed
  only after its own per-inode lock is acquired (waiting out any in-flight
  reader of that exact fd), and that wait happens AFTER ``_map_lock`` is
  released, so it never blocks an unrelated path's lookup.
"""

from __future__ import annotations

import os
import shutil
import stat
import threading
from pathlib import Path, PurePosixPath

_CHUNK = 1 << 20
_MAX_PINNED_FDS = 64

#: inode key -> the one live descriptor pinned for it.
_fds: dict[tuple[int, int], int] = {}
#: path -> the inode key it resolved to last time it was read (for retirement).
_path_inode: dict[Path, tuple[int, int]] = {}
#: inode key -> the set of tracked paths currently naming it (hard-link accounting).
_inode_paths: dict[tuple[int, int], set[Path]] = {}
#: inode key -> a lock held across that inode's own I/O; created lazily under `_map_lock`.
_inode_locks: dict[tuple[int, int], threading.Lock] = {}
#: guards only `_fds`/`_path_inode`/`_inode_paths`/`_inode_locks` mutation, never I/O.
_map_lock = threading.Lock()
#: FR02(b) race-loser descriptors: kept open forever, NEVER closed. Closing any fd this process
#: holds on an inode releases every POSIX fcntl lock the process holds on it via a *different* fd
#: too (the exact C15 hazard this module exists to prevent) -- so a redundant descriptor from a
#: stat/open race is tracked here (never a silent, unreachable leak) rather than closed.
_race_loser_fds: list[int] = []


class PinnedReadCapacityExceeded(OSError):
    """Raised instead of risking EMFILE when the pinned-fd cache is at its cap."""


def _open_parent_dir(anchor: Path, relative_path: str) -> tuple[int, str]:
    """Return `(parent_fd, name)`: an open dir_fd for *relative_path*'s immediate parent directory
    (each directory component opened ``O_NOFOLLOW`` relative to its own parent) and the final
    component's bare name. The caller owns *parent_fd* and must close it.

    A failed component open partway through the walk (a missing directory, a symlinked one refused
    by ``O_NOFOLLOW``) closes the currently-held ``parent`` before re-raising -- otherwise that
    descriptor would leak on every refused or missing path (core-316-sB round 2 review).
    """
    pure = PurePosixPath(relative_path)
    parts = pure.parts
    if not parts:
        raise ValueError("relative_path must not be empty")
    # O_NOFOLLOW refuses a symlink, but ``..`` is a real directory entry: only plain names stay below the anchor.
    # PurePosixPath drops interior "." parts, so a "." in the raw text is refused here too (FACTORY-ANCHOR-DOTDOT).
    if pure.is_absolute() or ".." in parts or "." in relative_path.split("/"):
        raise ValueError(f"relative_path must stay below the anchor: {relative_path!r}")
    *directories, name = parts
    parent = os.open(anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for directory in directories:
            child = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
    except BaseException:
        os.close(parent)
        raise
    return parent, name


def open_under(anchor: Path, relative_path: str) -> int:
    """Open *relative_path* under *anchor*, each component relative to its own parent: no component may be a symlink.

    *anchor* is opened directly (an ordinary, symlink-following open of the caller's own trusted root --
    the code-index walk anchors at ``repo_root``, unchanged from today's ``discovery.py::_open_under``);
    every component below it is opened ``O_NOFOLLOW`` off its immediate parent's descriptor, refusing a
    symlink swapped in anywhere below the anchor.
    """
    parent, name = _open_parent_dir(anchor, relative_path)
    try:
        return os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)


def delete_regular_file_under(anchor: Path, relative_path: str) -> bool:
    """Delete *relative_path* under *anchor*, but ONLY a genuine regular file with no symlinked
    component; returns whether a file was deleted.

    A missing file, a symlink at that exact path, or any other non-regular entry (e.g. a FIFO) is
    left untouched (a no-op, returns ``False``). Unlike ``open_under`` followed by a separate
    ``os.remove(anchor / relative_path)``, the unlink happens through the SAME already-open parent
    directory descriptor the verification open used (``os.unlink(name, dir_fd=parent)``), never a
    second resolution of the full path string -- closing a TOCTOU window where a parent directory
    swapped for a symlink between the verify and the delete could otherwise redirect the delete
    outside *anchor* entirely (PRD-CORE-316 P0 fix, core-316-sB round 1 review).
    """
    parent, name = _open_parent_dir(anchor, relative_path)
    try:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except OSError:  # trw-fail-silent-allow: missing or symlinked -- callers want this left untouched
            return False
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return False
        finally:
            os.close(fd)
        os.unlink(name, dir_fd=parent)
        return True
    finally:
        os.close(parent)


def _inode_lock_locked(key: tuple[int, int]) -> threading.Lock:
    """Get-or-create *key*'s per-inode lock. Caller must already hold ``_map_lock``."""
    lock = _inode_locks.get(key)
    if lock is None:
        lock = threading.Lock()
        _inode_locks[key] = lock
    return lock


def _retire_locked(path: Path, current: tuple[int, int]) -> tuple[tuple[int, int], int] | None:
    """Unlink *path* from its previous inode's bookkeeping and return `(stale_key, stale_fd)` to close outside the lock.

    Must be called with ``_map_lock`` held. Returns ``None`` when *path* has no previous record, when
    its inode is unchanged, or when another tracked path still names the previous inode (a hard link:
    FR02(a) -- that descriptor is never closed while a sibling path still claims it).
    """
    previous = _path_inode.get(path)
    if previous is None or previous == current:
        return None
    siblings = _inode_paths.get(previous)
    if siblings is not None:
        siblings.discard(path)
        if siblings:
            return None
        del _inode_paths[previous]
    stale_fd = _fds.pop(previous, None)
    if stale_fd is None:
        return None
    # The lock object stays in `_inode_locks` until `_close_stale` actually closes the descriptor:
    # popping it here would let a concurrent caller create a *different* Lock for the same key,
    # so `_close_stale`'s acquire would no longer wait on whatever reader is still mid-`pread` on it.
    return previous, stale_fd


def _close_stale(stale: tuple[tuple[int, int], int]) -> None:
    """Close a retired descriptor, unless its key was re-pinned while this call waited for its lock.

    Called with no lock held. Acquiring the stale key's own per-inode lock first means this never
    races an in-flight ``pread``/``copy_to`` on that same descriptor; because a different key is used
    for the caller's OWN (new) read, this never contends with the caller's own in-flight I/O.

    **P0 fix (worker-3 review, core-316-sA round 3):** between :func:`_retire_locked` unlinking
    *stale_key* and this call finally running, a DIFFERENT tracked path can re-pin the exact same
    inode (a rename-back, or a hard-link sibling that was never counted in ``_inode_paths``). Closing
    ``stale_fd`` unconditionally at that point would still drop every POSIX fcntl lock this process
    holds on that inode -- including the ones the NEW descriptor's connection now depends on -- because
    locks are scoped to (process, inode), not to the specific fd that opened them. So the check
    ("is *stale_key* back in ``_fds``?") and the close must be one atomic decision: both happen under
    ``_map_lock``, held across the close. **This is NFR02's single, deliberate exception** to "never
    hold the map lock across I/O": ``os.close`` on a regular file's descriptor is not blocking data
    I/O (no read/write transfer, just descriptor/lock bookkeeping the kernel completes immediately),
    so holding the already-short-lived map lock across it does not stall an unrelated inode's
    ``pread``/``copy_to``.
    """
    stale_key, stale_fd = stale
    with _map_lock:
        lock = _inode_lock_locked(stale_key)
    with lock, _map_lock:
        if stale_key in _fds:
            # Re-pinned while we waited for the lock: never close it (see the P0 fix above).
            # Track it exactly like a stat/open race-loser instead of silently discarding it.
            _race_loser_fds.append(stale_fd)
        else:
            os.close(stale_fd)
            _inode_locks.pop(stale_key, None)


def _evict_dead_paths() -> int:
    """Forget every tracked path that no longer exists; return how many entries were dropped.

    Runs only after a cap refusal, with no lock held. Snapshot ``_path_inode`` under ``_map_lock``,
    ``lstat`` each path outside it (I/O), then under ``_map_lock`` re-confirm each dead path still maps to
    the snapshotted inode and retire it through :func:`_retire_locked` (hard-link aware: a descriptor is
    only returned for closing when its last tracked path is gone). Descriptors are closed afterwards via
    :func:`_close_stale`, which waits out any in-flight ``pread`` on that inode. Only
    ``FileNotFoundError`` marks a path dead; a symlink, a replaced file or any other error stays live.
    """
    with _map_lock:
        snapshot = list(_path_inode.items())
    dead: list[tuple[Path, tuple[int, int]]] = []
    for path, key in snapshot:
        try:
            os.lstat(path)
        except FileNotFoundError:
            dead.append((path, key))
        except OSError:  # trw-fail-silent-allow: any error other than absence keeps the entry live
            continue
    stale: list[tuple[tuple[int, int], int]] = []
    evicted = 0
    with _map_lock:
        for path, key in dead:
            if _path_inode.get(path) != key:
                continue  # re-read or replaced since the snapshot: no longer the entry we judged dead
            retired = _retire_locked(path, (-1, -1))  # sentinel: "no inode", so previous is unlinked
            del _path_inode[path]
            evicted += 1
            if retired is not None:
                stale.append(retired)
    for item in stale:
        _close_stale(item)
    return evicted


def _pinned_fd_locked(path: Path) -> tuple[int, tuple[int, int], tuple[tuple[int, int], int] | None]:
    """Return `(fd, key, stale)` for *path*. Caller must already hold ``_map_lock``.

    *stale*, when not ``None``, is a retired `(key, fd)` the caller must close via :func:`_close_stale`
    AFTER releasing ``_map_lock`` (never inside it: closing can block on the stale key's own per-inode
    lock, and doing that while holding ``_map_lock`` would stall every unrelated path's lookup).
    """
    st = os.stat(path)
    key = (st.st_dev, st.st_ino)
    fd = _fds.get(key)
    if fd is None:
        # P2 fix (worker-3 review, core-316-sA round 3; NFR04/B71-08): a replaced mailbox or journal
        # file must not be permanently unrefreshable once the cache is at its cap. If *path* is the
        # ONLY tracked path naming its previous inode, retiring that association frees a slot net-zero
        # -- the cap check must account for that instead of refusing before `_retire_locked` ever runs.
        previous = _path_inode.get(path)
        frees_a_slot = previous is not None and previous != key and _inode_paths.get(previous) == {path}
        if len(_fds) >= _MAX_PINNED_FDS and not frees_a_slot:
            raise PinnedReadCapacityExceeded(
                f"pinned-read fd cache is at its {_MAX_PINNED_FDS}-entry cap; "
                f"refusing to open another descriptor for {path}"
            )
        opened_fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        opened = os.fstat(opened_fd)
        opened_key = (opened.st_dev, opened.st_ino)
        existing = _fds.get(opened_key)
        if existing is not None:
            # Stat-then-open race (FR02(b)): the file was replaced by an external actor between our
            # stat and our open, and the new inode was already pinned via a different path in the
            # meantime. NEVER close this redundant descriptor (see `_race_loser_fds`'s docstring) --
            # track it so it is accounted for, not a silent, unreachable leak.
            _race_loser_fds.append(opened_fd)
            fd = existing
        else:
            _fds[opened_key] = opened_fd
            fd = opened_fd
        key = opened_key
    # Retire the path's *previous* association only now that the new fd is safely resolved: retiring
    # first and then failing to open (capacity, OSError) would drop the stale tuple on the floor,
    # leaking a descriptor this module itself already unlinked from every dict that could close it.
    stale = _retire_locked(path, key)
    _path_inode[path] = key
    _inode_paths.setdefault(key, set()).add(path)
    return fd, key, stale


def _pinned_fd_for_io(path: Path) -> tuple[int, threading.Lock]:
    """Resolve *path*'s pinned fd and hold its per-inode lock across the caller's I/O, revalidating.

    Releasing ``_map_lock`` before acquiring the per-inode lock leaves a gap: a concurrent
    retirement can close the fd this call resolved to before it ever acquires that inode's lock
    (whoever gets the lock first wins, and Python's ``Lock`` gives no ordering guarantee). Once
    inside the lock, re-checking ``_fds[key]`` against the resolved fd catches that race: a mismatch
    means a retirement raced us while we waited, and the caller must re-resolve from scratch.

    The re-check must also confirm *lock* is still ``_inode_locks[key]``: ``(key, fd)`` alone is
    ABA-prone. After a retirement closes ``fd`` and pops *lock*, Linux readily hands the freed inode
    number to the next new file (overlayfs/ext4 do; APFS does not) and the lowest free fd number to
    the next ``os.open`` -- so the path can be re-pinned under the very same ``(key, fd)`` with a NEW
    per-inode lock. A reader then holding the orphaned lock would pass an fd-only check, and the next
    retirement (waiting on the new lock, not ours) could close the fd mid-``pread``: EBADF, or bytes
    from whatever file reuses that fd number next.
    """
    swept = False
    while True:
        try:
            with _map_lock:
                fd, key, stale = _pinned_fd_locked(path)
                lock = _inode_lock_locked(key)
        except PinnedReadCapacityExceeded:
            # At the cap: reclaim entries whose path is gone, once per call, then retry; if nothing was
            # dead the refusal stands (B71-08 unchanged for live descriptors).
            if swept or _evict_dead_paths() == 0:
                raise
            swept = True
            continue
        if stale is not None:
            _close_stale(stale)
        lock.acquire()
        with _map_lock:
            if _fds.get(key) == fd and _inode_locks.get(key) is lock:
                return fd, lock
        lock.release()  # raced: retired (or retired and re-pinned) while we waited; re-resolve


def read_at(path: Path, length: int, offset: int = 0) -> bytes:
    """Up to *length* bytes of *path* from *offset*; short only at end of file."""
    if os.name == "nt":
        with path.open("rb") as handle:
            handle.seek(offset)
            return handle.read(length)
    fd, lock = _pinned_fd_for_io(path)
    try:
        return os.pread(fd, length, offset)
    finally:
        lock.release()


def copy_to(path: Path, destination: Path) -> None:
    """Write *path*'s bytes to *destination* (created or truncated), like ``shutil.copyfile``."""
    if os.name == "nt":
        shutil.copyfile(path, destination)
        return
    fd, lock = _pinned_fd_for_io(path)
    try:
        with destination.open("wb") as out:
            offset = 0
            while chunk := os.pread(fd, _CHUNK, offset):
                out.write(chunk)
                offset += len(chunk)
    finally:
        lock.release()


__all__ = ["PinnedReadCapacityExceeded", "copy_to", "delete_regular_file_under", "open_under", "read_at"]
