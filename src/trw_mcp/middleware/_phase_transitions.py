"""Phase-transition refresh + capability detection — PRD-INTENT-002 FR04/FR05b.

Belongs to the ``middleware/phase_exposure.py`` facade — extracted so the
middleware stays under the 350 effective-LOC gate. Holds:

  - the per-session last-seen-phase ledger (transition detection),
  - the per-session advertised ``tools.listChanged`` capability flag (persisted
    at ``initialize``),
  - :func:`resolve_transition_action` — the FR05b capability×policy matrix,
  - :func:`emit_list_changed` — the FR04 notify path (fail-open).

All state is process-local; everything is fail-open so a transition-refresh
fault never blocks tool dispatch.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)

# session_id -> last phase the middleware observed for that session.
_last_phase: dict[str, str] = {}

# session_id -> whether the client advertised tools.listChanged at initialize.
_list_changed_capability: dict[str, bool] = {}

_VALID_ACTIONS = frozenset({"notify", "require_reconnect", "silent"})


def reset_transition_state() -> None:
    """Clear all transition + capability state — for testing only."""
    _last_phase.clear()
    _list_changed_capability.clear()


def detect_transition(session_id: str, phase: str) -> bool:
    """Return True when ``phase`` differs from the session's last-seen phase.

    The first observation for a session seeds the ledger and counts as a
    transition (the client's initial view may not match the resolved phase).
    """
    previous = _last_phase.get(session_id)
    _last_phase[session_id] = phase
    return previous != phase


def set_list_changed_capability(session_id: str, *, advertised: bool) -> None:
    """Persist the client's advertised ``tools.listChanged`` capability (FR05b)."""
    _list_changed_capability[session_id] = advertised


def client_supports_list_changed(session_id: str) -> bool:
    """Return the advertised capability flag; absent == unsupported (False)."""
    return _list_changed_capability.get(session_id, False)


def resolve_transition_action(*, advertised: bool, on_transition: str) -> str:
    """Resolve the FR05b (capability × profile-policy) → action.

    - capability advertised → ``notify`` always (no fallback needed).
    - otherwise → the profile's ``on_transition`` policy, defaulting to the
      safest ``require_reconnect`` when the policy is unknown/absent.
    """
    if advertised:
        return "notify"
    if on_transition in _VALID_ACTIONS:
        return on_transition
    return "require_reconnect"


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
    "client_supports_list_changed",
    "detect_transition",
    "emit_list_changed",
    "reset_transition_state",
    "resolve_transition_action",
    "set_list_changed_capability",
]
