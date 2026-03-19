"""Ceremony State Tracker for Universal Ceremony Enforcement (PRD-CORE-074 FR04).

Extended with context-reactive nudge engine (PRD-CORE-084).

Tracks what ceremony steps have been completed in the current session.
Persisted as JSON at .trw/context/ceremony-state.json.

Design constraints:
- All reads are fail-open: missing or corrupted file returns defaults, never raises.
- Writes are atomic: write to temp file then os.rename (POSIX atomic on same filesystem).
- JSON format (not YAML) for fast parsing.
- No external dependencies beyond stdlib + dataclasses.

Implementation is decomposed into three bounded-context modules:
- ``_nudge_state``    — dataclasses, persistence, mutation helpers
- ``_nudge_rules``    — decision logic (step completion, priority, reversion)
- ``_nudge_messages`` — message templates, formatting, assembly

This facade re-exports every public and private symbol so that all existing
``from trw_mcp.state.ceremony_nudge import X`` statements continue to work.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Re-exports from _nudge_messages (templates, formatting, assembly)
# ---------------------------------------------------------------------------
from trw_mcp.state._nudge_messages import (
    _HEADER as _HEADER,
)
from trw_mcp.state._nudge_messages import (
    _MINIMAL_HEADER as _MINIMAL_HEADER,
)
from trw_mcp.state._nudge_messages import (
    _STEP_RATIONALE as _STEP_RATIONALE,
)
from trw_mcp.state._nudge_messages import (
    _STEPS as _STEPS,
)
from trw_mcp.state._nudge_messages import (
    _assemble_nudge as _assemble_nudge,
)
from trw_mcp.state._nudge_messages import (
    _build_minimal_status_line as _build_minimal_status_line,
)
from trw_mcp.state._nudge_messages import (
    _build_status_line as _build_status_line,
)
from trw_mcp.state._nudge_messages import (
    _compute_urgency as _compute_urgency,
)
from trw_mcp.state._nudge_messages import (
    _context_reactive_message as _context_reactive_message,
)
from trw_mcp.state._nudge_messages import (
    _select_nudge_message as _select_nudge_message,
)

# ---------------------------------------------------------------------------
# Re-exports from _nudge_rules (decision logic)
# ---------------------------------------------------------------------------
from trw_mcp.state._nudge_rules import (
    _highest_priority_pending_step as _highest_priority_pending_step,
)
from trw_mcp.state._nudge_rules import (
    _next_two_steps as _next_two_steps,
)
from trw_mcp.state._nudge_rules import (
    _reversion_prompt as _reversion_prompt,
)
from trw_mcp.state._nudge_rules import (
    _step_complete as _step_complete,
)
from trw_mcp.state._nudge_rules import (
    is_local_model as is_local_model,
)

# ---------------------------------------------------------------------------
# Re-exports from _nudge_state (dataclasses, persistence, mutation)
# ---------------------------------------------------------------------------
from trw_mcp.state._nudge_state import (
    CeremonyState as CeremonyState,
)
from trw_mcp.state._nudge_state import (
    NudgeContext as NudgeContext,
)
from trw_mcp.state._nudge_state import (
    ToolName as ToolName,
)
from trw_mcp.state._nudge_state import (
    increment_files_modified as increment_files_modified,
)
from trw_mcp.state._nudge_state import (
    increment_learnings as increment_learnings,
)
from trw_mcp.state._nudge_state import (
    increment_nudge_count as increment_nudge_count,
)
from trw_mcp.state._nudge_state import (
    mark_build_check as mark_build_check,
)
from trw_mcp.state._nudge_state import (
    mark_checkpoint as mark_checkpoint,
)
from trw_mcp.state._nudge_state import (
    mark_deliver as mark_deliver,
)
from trw_mcp.state._nudge_state import (
    mark_review as mark_review,
)
from trw_mcp.state._nudge_state import (
    mark_session_started as mark_session_started,
)
from trw_mcp.state._nudge_state import (
    read_ceremony_state as read_ceremony_state,
)
from trw_mcp.state._nudge_state import (
    reset_ceremony_state as reset_ceremony_state,
)
from trw_mcp.state._nudge_state import (
    reset_nudge_count as reset_nudge_count,
)
from trw_mcp.state._nudge_state import (
    write_ceremony_state as write_ceremony_state,
)

# ---------------------------------------------------------------------------
# Main nudge computation (orchestrates rules + messages)
# ---------------------------------------------------------------------------


def compute_nudge(
    state: CeremonyState,
    available_learnings: int = 0,
    context: NudgeContext | None = None,
) -> str:
    """Compute the ceremony nudge message based on current state.

    When context is provided (PRD-CORE-084), uses context-reactive messages.
    When context is None, falls back to static urgency-tier messages (PRD-CORE-074).

    Priority order for pending step detection:
    1. session_start (if not called)
    2. checkpoint (if files_modified > 3 or no checkpoint in session)
    3. build_check (if phase >= validate and not run)
    4. review (if phase >= review and not called)
    5. deliver (if phase >= deliver)
    6. None (all complete — minimal status line)

    Returns:
        Nudge string to append to tool responses. Empty string if any error occurs.
        Budget: 600 chars with context, 400 chars without. Never blocks or refuses.
    """
    try:
        status_line = _build_status_line(state)
        header_and_status = f"{_HEADER}\n{status_line}"
        pending = _highest_priority_pending_step(state)

        if context is not None:
            # PRD-CORE-084: Context-reactive path
            urgency = _compute_urgency(state, pending) if pending else "low"
            reactive_msg = _context_reactive_message(context, state, urgency=urgency)

            if reactive_msg is None:
                # Unknown tool — fall back to static messages
                if pending is None:
                    return header_and_status
                nudge_msg = _select_nudge_message(pending, state, available_learnings)
                return _assemble_nudge(header_and_status, nudge_msg, budget=600)

            # Suppress reversion prompt when context-reactive message already
            # includes reversion guidance (DRY: build_check failure and review P0
            # reactive messages already contain reversion text).
            reversion: str | None = None
            _tools_with_reversion_in_reactive = (ToolName.BUILD_CHECK, ToolName.REVIEW)
            if context.tool_name in _tools_with_reversion_in_reactive:
                reversion = None  # reactive message already covers reversion
            else:
                reversion = _reversion_prompt(context, state)

            # Next-two-steps projection (only when no reactive message already
            # provides NEXT/THEN guidance)
            next_then_str: str | None = None
            # reactive_msg already contains NEXT/THEN for most tools, so skip

            return _assemble_nudge(
                header_and_status,
                reactive_msg,
                next_then=next_then_str,
                reversion=reversion,
                budget=600,
            )

        # PRD-CORE-074: Static urgency-tier path (no context)
        if pending is None:
            # All complete — single line
            return header_and_status

        # Try next-two-steps projection as a supplement
        nxt, then = _next_two_steps(state)
        next_then_str = None
        if nxt and then:
            rationale_nxt = _STEP_RATIONALE.get(nxt, "")
            rationale_then = _STEP_RATIONALE.get(then, "")
            next_then_str = f"NEXT: {nxt} ({rationale_nxt}). THEN: {then} ({rationale_then})."

        nudge_msg = _select_nudge_message(pending, state, available_learnings)
        full = f"{header_and_status}\n{nudge_msg}"

        # Enforce token limit (~400 chars) for static path
        if len(full) > 400:
            full = full[:397] + "..."

        return full
    except Exception:  # justified: fail-open — nudge must never raise or block tool responses
        return ""


def compute_nudge_minimal(state: CeremonyState, available_learnings: int = 0) -> str:
    """Compute a minimal ceremony nudge for local models.

    MINIMAL ceremony only nudges for session_start and deliver.
    Messages are capped at 50 tokens (~200 chars) instead of 100 tokens.
    Never raises (fail-open).
    """
    try:
        status_line = _build_minimal_status_line(state)

        # Determine the single pending step (only session_start or deliver)
        if not state.session_started:
            pending = "session_start"
        elif not state.deliver_called:
            pending = "deliver"
        else:
            pending = None

        if pending is None:
            # All complete — single compact line (well under 80 chars)
            return f"{_MINIMAL_HEADER}\n{status_line}"

        # Build a short message under 200 chars total
        if pending == "session_start":
            n = available_learnings
            if n > 0:
                msg = f"\u26a1 {n} prior learnings available. Call trw_session_start()."
            else:
                msg = "\u26a1 Call trw_session_start() to begin."
        else:  # deliver
            n = state.learnings_this_session
            if n > 0:
                msg = f"\u26a1 {n} learning(s) pending. Call trw_deliver() to persist."
            else:
                msg = "\u26a1 Call trw_deliver() to persist this session."

        full = f"{_MINIMAL_HEADER}\n{status_line}\n{msg}"

        # Enforce 200-char cap
        if len(full) > 200:
            full = full[:197] + "..."

        return full
    except Exception:  # justified: fail-open — nudge must never raise or block tool responses
        return ""
