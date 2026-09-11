"""Per-session phase observation and standard best-effort tool-list notification."""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)

# session_id -> last phase the middleware observed for that session.
_last_phase: dict[str, str] = {}


def reset_transition_state() -> None:
    """Clear transition state — for testing only."""
    _last_phase.clear()


def detect_transition(session_id: str, phase: str) -> bool:
    """Return True when ``phase`` differs from the session's last-seen phase.

    The first observation for a session seeds the ledger and counts as a
    transition (the client's initial view may not match the resolved phase).
    """
    previous = _last_phase.get(session_id)
    _last_phase[session_id] = phase
    return previous != phase


async def emit_list_changed(fastmcp_context: object | None) -> bool:
    """Emit ``notifications/tools/list_changed`` via the session (FR04).

    Fail-open: returns False (no crash) when the context/session is missing or
    the notification call raises.
    """
    if fastmcp_context is None:
        return False
    try:
        session = getattr(fastmcp_context, "session", None)
        send = getattr(session, "send_tool_list_changed", None)
        if send is None:
            return False
        await send()
        return True
    except Exception:  # justified: fail-open — a refresh fault must not block dispatch
        logger.warning("phase_list_changed_emit_failed", exc_info=True)
        return False


__all__ = [
    "detect_transition",
    "emit_list_changed",
    "reset_transition_state",
]
