"""Best-effort ``notifications/tools/list_changed`` emission.

The surface authority calls this when a session's resolved surface changes, so
a capable client re-fetches its tool list. The per-session phase ledger that
lived here served phase exposure only and went with it (PRD-CORE-300 S11a).
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


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


__all__ = ["emit_list_changed"]
