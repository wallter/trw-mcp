"""End the stdio server when its launching client dies (sprint-mcp7 W12).

A client that exits closes stdin and the server ends on EOF. But when anything
else still holds the pipe open, the server is reparented, never sees EOF, and
lives on as an orphan that keeps writing the checkout store. A daemon thread
compares the parent pid with the one at startup. On a change it sends SIGINT,
which cancels the event loop's tasks so in-flight handlers unwind, then
hard-exits: measured on fastmcp 3.x, shutdown otherwise waits forever on the
stdin reader thread, blocked in a read that only EOF would end.
"""

from __future__ import annotations

import os
import signal
import threading
import time

import structlog

__all__ = ["start_parent_watch"]

_logger = structlog.get_logger(__name__)


def _watch(parent: int, interval_s: float, grace_s: float) -> None:
    while os.getppid() == parent:
        time.sleep(interval_s)
    _logger.warning("parent_process_lost", parent_pid=parent, action="exit")
    os.kill(os.getpid(), signal.SIGINT)
    time.sleep(grace_s)
    os._exit(0)


def start_parent_watch(interval_s: float = 1.0, grace_s: float = 2.0) -> bool:
    """Start the watch; ``False`` when there is no parent to lose (already orphaned, or not POSIX)."""
    parent = os.getppid()
    if parent <= 1 or os.name != "posix":
        return False
    threading.Thread(target=_watch, args=(parent, interval_s, grace_s), name="trw-parent-watch", daemon=True).start()
    return True
