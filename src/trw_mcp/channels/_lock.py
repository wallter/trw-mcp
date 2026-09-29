"""ChannelLock — flock-based single-writer guarantee for channel files.

Thin context manager over the existing trw_mcp._locking shim.
Fail-open: raises ChannelLockSkip (not OSError) when the timeout elapses
so callers can return status="skipped_lock" without crashing.

PRD-DIST-2400 FR04/FR05.
"""

from __future__ import annotations

import errno
import os
import time
from contextlib import suppress
from pathlib import Path
from types import TracebackType

import structlog

from trw_mcp._checkout_write import append_checkout_file
from trw_mcp._locking import _lock_ex_nb, _lock_un

log = structlog.get_logger(__name__)

__all__ = ["ChannelLock", "ChannelLockSkip"]

_OPEN_FLAGS = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)

_POLL_INTERVAL_S: float = 0.010  # 10ms between attempts


class ChannelLockSkip(Exception):
    """Raised when ChannelLock cannot acquire within timeout_ms.

    Callers should treat this as a soft skip, not a hard error.
    """

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        super().__init__(f"Could not acquire channel lock within timeout: {lock_path}")


class ChannelLock:
    """Exclusive advisory lock on *lock_path*.

    Usage::

        try:
            with ChannelLock(Path(".trw/channels/ch.lock")):
                # single-writer section
                ...
        except ChannelLockSkip:
            return {"status": "skipped_lock"}

    On entry:
    - Creates *lock_path* (and its missing parent directories) beneath *root*
      without following a symlink (PRD-CORE-337 FR07): a planted link at the
      lock file or at any directory below *root* raises ``UnsafeWriteError``.
      *root* defaults to the lock file's own directory, which is then created
      by name and only the lock file itself is checked.
    - Opens *lock_path* with ``O_NOFOLLOW`` and acquires an exclusive flock via
      ``_lock_ex_nb``.  Retries every 10ms until ``timeout_ms`` elapses.
    - If the lock cannot be acquired in time, raises ``ChannelLockSkip``.

    On exit (normal or exception):
    - Calls ``_lock_un`` and closes the file descriptor.  Idempotent.

    Windows note: ``_locking.py`` provides a no-op shim on Windows where
    advisory locks are unavailable; concurrent access must be serialised by
    the caller in that environment.
    """

    def __init__(self, lock_path: Path, timeout_ms: int = 4000, *, root: Path | None = None) -> None:
        self.lock_path = lock_path
        self.timeout_ms = timeout_ms
        self.root = root
        self._fd: int | None = None

    def __enter__(self) -> ChannelLock:
        root = self.root
        if root is None:
            root = self.lock_path.parent
            root.mkdir(parents=True, exist_ok=True)
        # Create the lock file without following a symlink (an empty append), then open it for the
        # flock; the O_NOFOLLOW open refuses a leaf swapped for a link in between.
        append_checkout_file(root, self.lock_path, b"")
        self._fd = os.open(self.lock_path, _OPEN_FLAGS)

        deadline = time.monotonic() + self.timeout_ms / 1000.0
        # Single close-on-failure guard around the whole acquire loop. __exit__
        # is NOT called when __enter__ raises, so every exit EXCEPT a successful
        # `return self` must close the fd here. Catching BaseException (not just
        # OSError) is load-bearing: a KeyboardInterrupt or asyncio.CancelledError
        # interrupting the time.sleep below would otherwise escape the OSError
        # handler and leak the open fd until process exit (the 4s poll loop makes
        # that cancellation window real under the async FastMCP runtime).
        try:
            while True:
                try:
                    _lock_ex_nb(self._fd)
                    log.debug(
                        "channel_lock_acquired",
                        lock_path=str(self.lock_path),
                        timeout_ms=self.timeout_ms,
                    )
                    return self
                except OSError as exc:
                    if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                        # Lock is held by another process — retry
                        if time.monotonic() >= deadline:
                            log.debug(
                                "channel_lock_skip",
                                lock_path=str(self.lock_path),
                                timeout_ms=self.timeout_ms,
                            )
                            raise ChannelLockSkip(self.lock_path) from exc
                        time.sleep(_POLL_INTERVAL_S)
                    else:
                        # Unexpected OS error (permissions, etc.) — re-raise
                        raise
        except BaseException:
            self._close_fd()
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._fd is not None:
            try:
                _lock_un(self._fd)
            finally:
                self._close_fd()

    def _close_fd(self) -> None:
        if self._fd is not None:
            with suppress(OSError):
                os.close(self._fd)
            self._fd = None
