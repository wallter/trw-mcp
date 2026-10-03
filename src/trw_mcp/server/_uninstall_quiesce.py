"""Let a running ``.trw`` writer finish before uninstall removes the directory it writes into.

Belongs to ``_uninstall_corpus._remove_children``. The post-commit hook
asks for a detached ``trw-distill refresh-sidecars`` build that writes ``.trw/distill/map-cache`` while it holds an
``flock`` on ``.trw/distill/.sidecar-rebuild.lock`` (handed to the child); the post-commit worker itself holds
``.trw/runtime/post-commit.lock`` and the opt-in incremental run ``.trw/distill/.incremental.lock``.
A removal walk that overlaps a writer sees ``ENOTEMPTY`` (the writer created a file after the walk listed the
directory), and a retry alone is not enough because the writer can recreate the directory afterwards
(UNINSTALL-DISTILL-RACE). So uninstall first takes EVERY one of those locks, creating the lock file when it is
absent: a writer that starts later finds the lock held and defers (its acquire is non-blocking), where an absent
lock would have let it recreate ``.trw/runtime`` mid-removal. A live holder gets a bounded wait; one that outlives
it is never ignored: :class:`WriterStillRunning` is raised and uninstall keeps ``.trw``, names the holder and exits
non-zero. The locks are held for the whole removal.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Iterator
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: no flock, so no writer to wait for either
    fcntl = None  # type: ignore[assignment]

#: Lock files, relative to the ``.trw`` directory, that a background writer holds for its lifetime.
WRITER_LOCKS: tuple[Path, ...] = (
    Path("runtime") / "post-commit.lock",
    Path("distill") / ".incremental.lock",
    Path("distill") / ".sidecar-rebuild.lock",
)
#: The top-level directories that carry those locks: uninstall removes them last, so the locks outlive the rest of the walk.
WRITER_DIRS: frozenset[str] = frozenset(rel.parts[0] for rel in WRITER_LOCKS)
#: How long uninstall waits for one live holder, and how often it looks.
WAIT_SECONDS: float = 10.0
POLL_SECONDS: float = 0.1
#: How many times one lock may be found already retired (its holder unlinked it between our open and our look) before it is reported like a holder that outlived
#: the wait, never chased: lock churn that does not settle must not spin (UNINSTALL-QUIESCE-KIS).
MAX_RETIRED_RETRIES: int = 50


class WriterStillRunning(OSError):
    """A background writer still holds one of :data:`WRITER_LOCKS` after the wait; nothing was removed."""

    def __init__(self, rel: Path, holder: str, waited: float) -> None:
        self.lock = rel.as_posix()
        super().__init__(
            f"{self.lock} is held by a live TRW writer ({holder}) that did not finish within {waited:g}s; "
            ".trw was left in place. Let it finish (or stop it), then run uninstall again."
        )


def _holder(fd: int) -> str:
    """Who holds the lock, from the pid the post-commit worker records in it; ``pid unknown`` otherwise."""
    try:
        raw = os.pread(fd, 512, 0).decode("utf-8", errors="replace").strip()
        pid = json.loads(raw).get("pid") if raw.startswith("{") else (int(raw) if raw.isdigit() else None)
    except (OSError, ValueError, AttributeError):  # trw-fail-silent-allow: the pid is only a courtesy in the message
        pid = None
    return f"pid {pid}" if isinstance(pid, int) else "pid unknown"


def _open_lock(trw_fd: int, rel: Path) -> tuple[int, int] | None:
    """Open (creating, with its directory, when absent) *rel* under the ``.trw`` fd: ``(parent_fd, lock_fd)``.

    ``None`` when the path cannot be opened without following a link: the walk refuses those too.
    """
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        with contextlib.suppress(FileExistsError):
            os.mkdir(rel.parent, 0o700, dir_fd=trw_fd)
        parent = os.open(rel.parent, flags, dir_fd=trw_fd)
    except OSError:  # trw-fail-silent-allow: a link or file where the directory belongs: left to the walk's own refusal
        return None
    try:
        return parent, os.open(rel.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    except OSError:  # trw-fail-silent-allow: a link planted at the lock path is refused by O_NOFOLLOW, like the walk
        os.close(parent)
        return None


def _take(trw_fd: int, rel: Path, wait: float) -> int:
    """``flock`` the lock file at *rel* (created when absent), waiting up to *wait* seconds for a live holder.

    Returns the descriptor to keep open, or ``-1`` where nothing can be held (no ``flock`` on the platform, or a
    link in the way). Raises :class:`WriterStillRunning` when a holder outlives the wait.
    """
    if fcntl is None:
        return -1
    waited, retired = 0.0, 0
    while True:
        opened = _open_lock(trw_fd, rel)
        if opened is None:
            return -1
        parent, fd = opened
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if os.fstat(fd).st_ino == os.stat(rel.name, dir_fd=parent).st_ino:
                os.close(parent)
                return fd
            # A holder unlinks its lock on release: ours is a retired inode, so lock the one at the path.
            retired += 1
        except OSError as exc:
            if isinstance(
                exc, FileNotFoundError
            ):  # trw-fail-silent-allow: the path vanished under us: look again, counted like a retired inode
                retired += 1
            elif waited >= wait:
                holder = _holder(fd)
                os.close(fd)
                os.close(parent)
                raise WriterStillRunning(rel, holder, wait) from exc
            else:  # EWOULDBLOCK: a live writer holds it
                time.sleep(POLL_SECONDS)  # looked up per call so a test can stand in for the clock
                waited += POLL_SECONDS
        os.close(fd)
        os.close(parent)
        if retired >= MAX_RETIRED_RETRIES:
            raise WriterStillRunning(rel, f"its lock file was replaced {retired} times in a row", wait)


@contextlib.contextmanager
def quiesced_writers(trw_fd: int, *, wait: float | None = None) -> Iterator[None]:
    """Hold every background-writer lock under the ``.trw`` directory fd *trw_fd* for the ``with`` body.

    Raises :class:`WriterStillRunning` (releasing whatever it already took) if any holder outlives the wait.
    """
    limit = WAIT_SECONDS if wait is None else wait
    held: list[int] = []
    try:
        for rel in WRITER_LOCKS:
            fd = _take(trw_fd, rel, limit)
            if fd >= 0:
                held.append(fd)
        yield
    finally:
        for fd in held:
            os.close(fd)  # closing drops the flock
