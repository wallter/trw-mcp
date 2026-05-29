"""Tests for _lock.py — ChannelLock context manager."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

from trw_mcp.channels._lock import ChannelLock, ChannelLockSkip


# ---------------------------------------------------------------------------
# Basic acquire / release
# ---------------------------------------------------------------------------


def test_channel_lock_acquires_and_releases(tmp_path: Path) -> None:
    lock_file = tmp_path / "ch.lock"
    with ChannelLock(lock_file):
        assert lock_file.exists()
    # After context exit, file still exists (lock released, not deleted)
    assert lock_file.exists()


def test_channel_lock_creates_parent_dirs(tmp_path: Path) -> None:
    lock_file = tmp_path / "deep" / "nested" / "ch.lock"
    assert not lock_file.parent.exists()
    with ChannelLock(lock_file):
        assert lock_file.parent.exists()


def test_channel_lock_releases_on_exception(tmp_path: Path) -> None:
    """Lock is released even when an exception occurs inside the context."""
    lock_file = tmp_path / "ch.lock"
    with pytest.raises(ValueError):
        with ChannelLock(lock_file):
            raise ValueError("intentional")
    # After exception, the lock should be released.
    # Verify by acquiring it again successfully.
    with ChannelLock(lock_file):
        pass


def test_channel_lock_sequential_reacquire(tmp_path: Path) -> None:
    """Same lock file can be acquired sequentially multiple times."""
    lock_file = tmp_path / "ch.lock"
    for _ in range(3):
        with ChannelLock(lock_file):
            pass


# ---------------------------------------------------------------------------
# ChannelLockSkip on timeout
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows advisory locking is a no-op — skip contention test",
)
def test_channel_lock_skip_on_timeout(tmp_path: Path) -> None:
    """Background thread holds lock; main thread sees ChannelLockSkip within 4100ms."""
    lock_file = tmp_path / "ch.lock"

    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def holder() -> None:
        with ChannelLock(lock_file, timeout_ms=5000):
            lock_acquired.set()
            release_lock.wait(timeout=10.0)

    t = threading.Thread(target=holder, daemon=True)
    t.start()
    lock_acquired.wait(timeout=5.0)

    start = time.monotonic()
    with pytest.raises(ChannelLockSkip) as exc_info:
        with ChannelLock(lock_file, timeout_ms=200):
            pass
    elapsed = time.monotonic() - start

    assert elapsed < 1.0  # Should fail fast (200ms + overhead)
    assert "ch.lock" in str(exc_info.value.lock_path)

    release_lock.set()
    t.join(timeout=5.0)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows advisory locking is a no-op — skip contention test",
)
def test_channel_lock_skip_never_deadlocks(tmp_path: Path) -> None:
    """Verify no deadlock: contended lock times out cleanly."""
    lock_file = tmp_path / "ch.lock"

    holder_entered = threading.Event()
    holder_release = threading.Event()
    skip_raised = threading.Event()

    def hold() -> None:
        with ChannelLock(lock_file, timeout_ms=5000):
            holder_entered.set()
            holder_release.wait(timeout=10.0)

    def attempt() -> None:
        holder_entered.wait(timeout=5.0)
        try:
            with ChannelLock(lock_file, timeout_ms=100):
                pass
        except ChannelLockSkip:
            skip_raised.set()

    t1 = threading.Thread(target=hold, daemon=True)
    t2 = threading.Thread(target=attempt, daemon=True)
    t1.start()
    t2.start()
    t2.join(timeout=5.0)
    holder_release.set()
    t1.join(timeout=5.0)

    assert skip_raised.is_set(), "ChannelLockSkip should have been raised"
    assert not t1.is_alive(), "Holder thread should have exited"
    assert not t2.is_alive(), "Attempt thread should have exited"


# ---------------------------------------------------------------------------
# Lock lifecycle metadata
# ---------------------------------------------------------------------------


def test_channel_entry_lock_lifecycle_default() -> None:
    """ChannelEntry.lock_lifecycle default is auto_cleanup_on_channel_disable."""
    from trw_mcp.channels._manifest_models import ChannelEntry, ChannelSurface

    entry = ChannelEntry(
        id="ch1",
        client="codex",
        surface=ChannelSurface.AGENTS_MD_SEGMENT,
        telemetry_tag="t",
    )
    assert entry.lock_lifecycle == "auto_cleanup_on_channel_disable"
