"""Shared AGENTS.md lock for opencode distill and ceremony writers.

Provides a single canonical lock path (``.trw/channels/agents-md.lock``)
that BOTH the ceremony writer (``generate_agents_md()``) and the distill
segment writer (``install_opencode_agents_md_distill_segment()``) must
acquire before writing AGENTS.md.

This prevents interleaved content when both writers are triggered
concurrently (e.g., session_start fires distill refresh at the same time
as an operator-triggered ``trw-mcp update-project``).

Audit fix P0-06.

PRD-DIST-2403 FR05.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.channels._lock import ChannelLock, ChannelLockSkip

__all__ = [
    "AGENTS_MD_LOCK_PATH",
    "ChannelLockSkip",
    "agents_md_lock",
]

# Canonical shared lock path — relative to repo_root.
AGENTS_MD_LOCK_PATH = ".trw/channels/agents-md.lock"


def agents_md_lock(repo_root: Path, *, timeout_ms: int = 4000) -> ChannelLock:
    """Return a ``ChannelLock`` for the shared AGENTS.md lock file.

    Both the ceremony writer and the distill segment writer must call this
    function and acquire the returned lock before modifying AGENTS.md.

    Usage::

        try:
            with agents_md_lock(repo_root):
                # read-merge-write AGENTS.md
                ...
        except ChannelLockSkip:
            return {"status": "skipped_lock"}

    Args:
        repo_root: Repository root directory.
        timeout_ms: Lock acquisition timeout in milliseconds (default 4000).

    Returns:
        A ``ChannelLock`` instance targeting the shared lock path.

    Raises:
        ChannelLockSkip: If the lock cannot be acquired within *timeout_ms*.
    """
    lock_path: Path = repo_root / AGENTS_MD_LOCK_PATH
    return ChannelLock(lock_path, timeout_ms=timeout_ms)
