"""Status-line builders — extracted from _nudge_messages.py for module-size compliance.

Belongs to the ``_nudge_messages.py`` facade. Re-exported there for back-compat
with ``ceremony_nudge.py`` which imports all 4 status-line builders.

Four functions:
- ``_build_status_line`` — full checkmark/cross status line for all ceremony steps
- ``_build_minimal_status_line`` — compact session_start + deliver only
- ``_build_done_next_then_status`` — Done/Next/Then format (PRD-CORE-125 FR04)
- ``_build_done_next_then_status_light`` — light-mode Done/Next/Then
"""

from __future__ import annotations

from trw_mcp.state._nudge_state import CeremonyState, _step_complete


def _build_minimal_status_line(state: CeremonyState) -> str:
    """Build a compact status line covering only session_start and deliver."""
    start_mark = "✓" if state.session_started else "✗"
    deliver_mark = "✓" if state.deliver_called else "✗"
    return f"{start_mark} start | {deliver_mark} deliver"


# WHY rationale for Next/Then lines (short, consequence-oriented)
_DONE_NEXT_RATIONALE: dict[str, str] = {
    "session_start": "loads prior learnings",
    "checkpoint": "saves progress against context loss",
    "build_check": "catches integration issues before delivery",
    "review": "independent verification catches spec drift",
    "deliver": "persists your learnings for future agents",
}


def _build_done_next_then_status_light(state: CeremonyState) -> str:
    """Build a compact Done/Next/Then status line for light-mode clients.

    PRD-CORE-125 FR04: Single pipe-separated line under 100 characters.
    Format: Done: session_start | Next: learn — record what you found | Then: deliver
    """
    # Build "Done" items (abbreviated)
    done_items: list[str] = []
    if _step_complete("session_start", state):
        done_items.append("session_start")
    if state.learnings_this_session > 0:
        done_items.append(f"learn({state.learnings_this_session})")
    if _step_complete("deliver", state):
        done_items.append("deliver")

    # Light mode only tracks: session_start, learn, deliver
    pending: list[str] = []
    if not state.session_started:
        pending.append("session_start")
    if state.learnings_this_session == 0:
        pending.append("learn")
    if not state.deliver_called:
        pending.append("deliver")

    parts: list[str] = []
    if done_items:
        parts.append(f"Done: {', '.join(done_items)}")
    if len(pending) >= 1:
        rationale = _DONE_NEXT_RATIONALE.get(pending[0], "")
        short_rationale = rationale[:30] if len(rationale) > 30 else rationale
        parts.append(f"Next: {pending[0]} — {short_rationale}")
    if len(pending) >= 2:
        parts.append(f"Then: {pending[1]}")

    result = " | ".join(parts)
    # Enforce 100-char budget
    if len(result) > 100:
        result = result[:97] + "..."
    return result
