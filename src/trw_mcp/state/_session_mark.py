"""The trw-mcp server's one session high-water mark (PRD-SEC-023 FR04).

Belongs to the recall/learn paths. Each server process holds ONE :class:`~trw_memory.labels.SessionMark`: it starts at ``team``, every row a
recall returns raises it, and a restart or reconnect is the only reset (the mark is per process in phase 0). A row written while the mark is
above the row's own label is stamped with it, so the label follows the data into every later session of every project.
"""

from __future__ import annotations

from trw_memory.labels import SessionMark

__all__ = ["reset_session_mark", "session_egress_refusal", "session_mark"]

_MARK = SessionMark()


def session_mark() -> SessionMark:
    """This process's mark."""
    return _MARK


def session_egress_refusal() -> str | None:
    """Why session text (feedback, a shared-recall query) may not leave the host now, or ``None`` while the mark is ``team`` (PRD-SEC-023 FR06).

    The text a session writes may restate what it read, so once the mark is above ``team`` (the platform's clearance) no session text is sent.
    The message names the level only, never what was read, and the remedy.
    """
    label = _MARK.reported()
    if label is None:
        return None
    return f"this session read a row labelled {label}; nothing it writes is sent off this machine. Start a new session to send it"


def reset_session_mark() -> None:
    """Start a new session: the mark returns to ``team``. Production never calls this; a new process is the reset. Tests do."""
    global _MARK
    _MARK = SessionMark()
