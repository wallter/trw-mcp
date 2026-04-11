"""Legacy ceremony-nudge compatibility surface.

Live tool paths are isolated behind ``ceremony_progress`` and
``tools._ceremony_status``. This module remains available for offline/legacy
nudge callers and re-exports the legacy APIs without owning live wiring.
"""

from __future__ import annotations

import structlog

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
    _build_done_next_then_status as _build_done_next_then_status,
)
from trw_mcp.state._nudge_messages import (
    _build_done_next_then_status_light as _build_done_next_then_status_light,
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
    _select_nudge_pool as _select_nudge_pool,
)
from trw_mcp.state._nudge_rules import (
    _step_complete as _step_complete,
)
from trw_mcp.state._nudge_rules import (
    apply_pool_cooldown as apply_pool_cooldown,
)
from trw_mcp.state._nudge_rules import (
    is_local_model as is_local_model,
)
from trw_mcp.state._nudge_rules import (
    is_pool_in_cooldown as is_pool_in_cooldown,
)
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
    increment_tool_call_counter as increment_tool_call_counter,
)
from trw_mcp.state._nudge_state import (
    is_nudge_eligible as is_nudge_eligible,
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
    record_nudge_shown as record_nudge_shown,
)
from trw_mcp.state._nudge_state import (
    record_pool_ignore as record_pool_ignore,
)
from trw_mcp.state._nudge_state import (
    record_pool_nudge as record_pool_nudge,
)
from trw_mcp.state._nudge_state import (
    reset_ceremony_state as reset_ceremony_state,
)
from trw_mcp.state._nudge_state import (
    reset_nudge_count as reset_nudge_count,
)
from trw_mcp.state._nudge_state import (
    set_ceremony_phase as set_ceremony_phase,
)
from trw_mcp.state._nudge_state import (
    write_ceremony_state as write_ceremony_state,
)

logger = structlog.get_logger(__name__)


def compute_nudge(
    state: CeremonyState,
    available_learnings: int = 0,
    context: NudgeContext | None = None,
) -> str:
    """Compute the ceremony nudge message based on current state."""

    try:
        from trw_mcp.models.config._loader import get_config

        config = get_config()
        if not config.effective_nudge_enabled:
            return ""

        budget = config.nudge_budget_chars
        weights = config.client_profile.nudge_pool_weights
        cooldown_after = config.nudge_pool_cooldown_after
        cooldown_calls = config.nudge_pool_cooldown_calls

        pool = _select_nudge_pool(state, weights, context, cooldown_after, cooldown_calls)
        status = _build_minimal_status_line(state)
        header_status = f"{_MINIMAL_HEADER}\n{status}"

        if pool is None:
            return header_status

        content = ""
        if pool == "workflow":
            from trw_mcp.state._nudge_content import load_pool_message

            content = load_pool_message("workflow", phase_hint=state.phase)
        elif pool == "learnings":
            if available_learnings > 0:
                content = _select_nudge_message("session_start", state, available_learnings)
            else:
                pending = _highest_priority_pending_step(state)
                if pending:
                    content = _select_nudge_message(pending, state, available_learnings)
        elif pool == "ceremony":
            pending = _highest_priority_pending_step(state)
            if pending:
                from trw_mcp.state._nudge_content import load_pool_message

                content = load_pool_message("ceremony", phase_hint=pending)
            if not content and pending:
                content = _select_nudge_message(pending, state, available_learnings)
        elif pool == "context":
            urgency = _compute_urgency(
                state,
                _highest_priority_pending_step(state) or "session_start",
            )
            reactive = _context_reactive_message(context, state, urgency=urgency) if context else None
            content = reactive or ""

        if not content:
            return header_status

        logger.debug("nudge_pool_selected", pool=pool)
        return _assemble_nudge(header_status, content, budget=budget)
    except Exception:
        logger.debug("compute_nudge_failed", exc_info=True)
        return ""


def compute_nudge_minimal(state: CeremonyState, available_learnings: int = 0) -> str:
    """Compute a minimal ceremony nudge for local models."""

    try:
        status_line = _build_minimal_status_line(state)
        if not state.session_started:
            pending = "session_start"
        elif not state.deliver_called:
            pending = "deliver"
        else:
            pending = None

        if pending is None:
            return f"{_MINIMAL_HEADER}\n{status_line}"

        if pending == "session_start":
            if available_learnings > 0:
                msg = f"\u26a1 {available_learnings} prior learnings available. Call trw_session_start()."
            else:
                msg = "\u26a1 Call trw_session_start() to begin."
        else:
            if state.learnings_this_session > 0:
                msg = f"\u26a1 {state.learnings_this_session} learning(s) pending. Call trw_deliver() to persist."
            else:
                msg = "\u26a1 Call trw_deliver() to persist this session."

        full = f"{_MINIMAL_HEADER}\n{status_line}\n{msg}"
        return full if len(full) <= 200 else full[:197] + "..."
    except Exception:
        return ""
