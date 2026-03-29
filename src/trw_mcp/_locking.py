"""Portable advisory file-locking shim.

``fcntl.flock`` is Unix-only.  On Windows the functions degrade to no-ops
because Windows uses mandatory locking at the OS level and ``fcntl``-style
advisory locks are unavailable.

Every module that needs file locking imports from here instead of inlining
its own ``try: import fcntl`` guard.  This keeps the platform check in one
place (DRY) and makes mocking straightforward in tests.
"""

from __future__ import annotations

__all__ = [
    "_lock_ex",
    "_lock_ex_nb",
    "_lock_sh",
    "_lock_un",
]

try:
    import fcntl as _fcntl

    def _lock_sh(fd: int) -> None:
        """Acquire a shared (read) advisory lock on *fd*."""
        _fcntl.flock(fd, _fcntl.LOCK_SH)

    def _lock_ex(fd: int) -> None:
        """Acquire an exclusive (write) advisory lock on *fd*."""
        _fcntl.flock(fd, _fcntl.LOCK_EX)

    def _lock_ex_nb(fd: int) -> None:
        """Try to acquire an exclusive lock without blocking.

        Raises ``OSError`` (``errno.EWOULDBLOCK``) if the lock is held.
        """
        _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)

    def _lock_un(fd: int) -> None:
        """Release the advisory lock on *fd*."""
        _fcntl.flock(fd, _fcntl.LOCK_UN)

except ImportError:  # Windows — advisory locking is a no-op

    def _lock_sh(fd: int) -> None:  # noqa: ARG001
        """No-op: advisory locks unavailable on this platform."""

    def _lock_ex(fd: int) -> None:  # noqa: ARG001
        """No-op: advisory locks unavailable on this platform."""

    def _lock_ex_nb(fd: int) -> None:  # noqa: ARG001
        """No-op: advisory locks unavailable on this platform."""

    def _lock_un(fd: int) -> None:  # noqa: ARG001
        """No-op: advisory locks unavailable on this platform."""
