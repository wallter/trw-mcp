"""Tests for channels/opencode/_shared_lock.py — shared AGENTS.md lock.

PRD-DIST-2403 FR05 / audit P0-06.
"""

from __future__ import annotations

from pathlib import Path


def test_agents_md_lock_returns_channel_lock(tmp_path: Path) -> None:
    """agents_md_lock returns a ChannelLock instance."""
    from trw_mcp.channels._lock import ChannelLock
    from trw_mcp.channels.opencode._shared_lock import agents_md_lock

    lock = agents_md_lock(tmp_path)
    assert isinstance(lock, ChannelLock)


def test_agents_md_lock_path_is_canonical(tmp_path: Path) -> None:
    """Lock path is .trw/channels/agents-md.lock relative to repo_root."""
    from trw_mcp.channels.opencode._shared_lock import (
        AGENTS_MD_LOCK_PATH,
        agents_md_lock,
    )

    lock = agents_md_lock(tmp_path)
    expected_path = tmp_path / AGENTS_MD_LOCK_PATH
    assert lock.lock_path == expected_path


def test_shared_lock_path_constant() -> None:
    """AGENTS_MD_LOCK_PATH has the canonical value."""
    from trw_mcp.channels.opencode._shared_lock import AGENTS_MD_LOCK_PATH

    assert AGENTS_MD_LOCK_PATH == ".trw/channels/agents-md.lock"


def test_shared_lock_acquires_and_releases(tmp_path: Path) -> None:
    """ChannelLock can be acquired and released without error."""
    from trw_mcp.channels.opencode._shared_lock import agents_md_lock

    lock = agents_md_lock(tmp_path)
    lock.__enter__()
    lock.__exit__(None, None, None)
    # Lock file should exist after acquisition
    lock_file = tmp_path / ".trw" / "channels" / "agents-md.lock"
    assert lock_file.exists()


def test_shared_lock_skip_on_concurrent_hold(tmp_path: Path) -> None:
    """Second acquisition attempt raises ChannelLockSkip when lock is held."""
    import threading

    from trw_mcp.channels._lock import ChannelLockSkip
    from trw_mcp.channels.opencode._shared_lock import agents_md_lock

    barrier = threading.Event()
    skip_raised = threading.Event()

    def holder() -> None:
        lock = agents_md_lock(tmp_path, timeout_ms=4000)
        lock.__enter__()
        barrier.set()  # signal that lock is held
        import time
        time.sleep(0.2)  # hold for 200ms
        lock.__exit__(None, None, None)

    def contender() -> None:
        barrier.wait()  # wait until lock is held
        try:
            lock2 = agents_md_lock(tmp_path, timeout_ms=50)  # short timeout
            lock2.__enter__()
            lock2.__exit__(None, None, None)
        except ChannelLockSkip:
            skip_raised.set()

    t1 = threading.Thread(target=holder)
    t2 = threading.Thread(target=contender)
    t1.start()
    t2.start()
    t1.join(timeout=1.0)
    t2.join(timeout=1.0)

    assert skip_raised.is_set(), "Expected ChannelLockSkip when lock is contended"
