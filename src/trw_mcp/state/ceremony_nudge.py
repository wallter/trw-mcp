"""Legacy ceremony-nudge compatibility surface.

Live tool paths are isolated behind ``ceremony_progress`` and
``tools._ceremony_status``. This module remains available for offline/legacy
nudge callers and re-exports the legacy APIs without owning live wiring.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from trw_mcp.models.config._client_profile import ClientProfile

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
    _emit_debug_capture_event as _emit_debug_capture_event,
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
    profile: ClientProfile | None = None,
) -> str:
    """Compute the ceremony nudge message based on current state.

    PRD-CORE-149 FR03: when ``profile`` is omitted, the active
    :class:`ClientProfile` is resolved from :func:`get_config` so nudge
    templates pipe through :func:`format_nudge` with the correct client
    identity. Callers that already have a profile in hand (tests, offline
    tooling) can pass one explicitly.
    """

    try:
        from trw_mcp.models.config._loader import get_config

        config = get_config()
        if not config.effective_nudge_enabled:
            return ""

        if profile is None:
            profile = config.client_profile

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
                content = _select_nudge_message("session_start", state, available_learnings, profile=profile)
            else:
                pending = _highest_priority_pending_step(state)
                if pending:
                    content = _select_nudge_message(pending, state, available_learnings, profile=profile)
        elif pool == "ceremony":
            pending = _highest_priority_pending_step(state)
            if pending:
                from trw_mcp.state._nudge_content import load_pool_message

                content = load_pool_message("ceremony", phase_hint=pending)
            if not content and pending:
                content = _select_nudge_message(pending, state, available_learnings, profile=profile)
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
    except Exception:  # justified: fail-open -- legacy/offline nudge rendering must not break callers
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
    except Exception:  # justified: fail-open -- minimal legacy nudge rendering must not break callers
        logger.debug("compute_nudge_minimal_failed", exc_info=True)
        return ""


def compute_nudge_learning_injection(
    state: CeremonyState,
    trw_dir: Path,
    context: NudgeContext | None = None,
) -> str:
    """Surface a task-relevant prior learning in the ceremony nudge slot."""

    del context  # reserved for future context-aware refinements

    try:
        content, _, _ = select_learning_injection_content(state, trw_dir)
        if content:
            return content
        return compute_nudge_minimal(state)
    except Exception:  # justified: fail-open -- recall issues must not break ceremony status
        logger.debug("compute_nudge_learning_injection_failed", exc_info=True)
        return compute_nudge_minimal(state)


def compute_nudge_contextual(
    state: CeremonyState,
    trw_dir: Path,
    context: NudgeContext | None = None,
) -> str:
    """Render a workflow scaffold plus one phase-aware next action."""

    try:
        content, _, _ = select_contextual_nudge_content(state, trw_dir, context=context)
        if content:
            return content
        return compute_nudge_minimal(state)
    except Exception:  # justified: fail-open -- recall issues must not break ceremony status
        logger.debug("compute_nudge_contextual_failed", exc_info=True)
        return compute_nudge_minimal(state)


def compute_nudge_contextual_action(
    state: CeremonyState,
    trw_dir: Path,
    context: NudgeContext | None = None,
) -> str:
    """Render the contextual next-step scaffold without the recall caution line."""

    try:
        content, _, _ = select_contextual_nudge_content(
            state,
            trw_dir,
            context=context,
            include_learning_caution=False,
        )
        if content:
            return content
        return compute_nudge_minimal(state)
    except Exception:  # justified: fail-open -- recall issues must not break ceremony status
        logger.debug("compute_nudge_contextual_action_failed", exc_info=True)
        return compute_nudge_minimal(state)


def _is_agent_in_distress(state: CeremonyState) -> bool:
    """Detect if the agent is stalled, failing, or at start-of-session.

    PRD-CORE-145/146: Distress is defined as:
    1.  Startup: session_started is False.
    2.  Stalled: >10 turns without a checkpoint (prevents loops).
    3.  Failing: Last build check failed (needs correction).
    """
    stalled = (state.tool_call_counter - state.last_checkpoint_turn) > 10
    repeated_failure = state.build_check_result == "failed"
    startup = not state.session_started
    return startup or stalled or repeated_failure


def compute_nudge_contextual_distress(
    state: CeremonyState,
    trw_dir: Path,
    context: NudgeContext | None = None,
) -> str:
    """Render rich contextual nudge only if the agent appears stalled or in distress.

    Falls back to compute_nudge_minimal during 'Flow State' (non-distress).
    """

    try:
        if _is_agent_in_distress(state):
            return compute_nudge_contextual(state, trw_dir, context=context)

        return compute_nudge_minimal(state)
    except Exception:  # justified: fail-open per NFR02
        logger.debug("compute_nudge_contextual_distress_failed", exc_info=True)
        return compute_nudge_minimal(state)



# Specialized compute_nudge_* variants extracted to _ceremony_nudge_specialized
# (PRD-DIST-243 batch 24). Re-exported for back-compat with _ceremony_status.py.
from trw_mcp.state._ceremony_nudge_specialized import (
    compute_nudge_anchor as compute_nudge_anchor,
)
from trw_mcp.state._ceremony_nudge_specialized import (
    compute_nudge_cod as compute_nudge_cod,
)
from trw_mcp.state._ceremony_nudge_specialized import (
    compute_nudge_governance as compute_nudge_governance,
)
from trw_mcp.state._ceremony_nudge_specialized import (
    compute_nudge_negative as compute_nudge_negative,
)
from trw_mcp.state._ceremony_nudge_specialized import (
    compute_nudge_silent_flow as compute_nudge_silent_flow,
)
from trw_mcp.state._ceremony_nudge_specialized import (
    compute_nudge_stepback as compute_nudge_stepback,
)




# Selectors extracted to _ceremony_nudge_selectors (PRD-DIST-243 batch 25).
# Re-exported for back-compat with _ceremony_status.py.
from trw_mcp.state._ceremony_nudge_selectors import (
    _contextual_next_step_message as _contextual_next_step_message,
)
from trw_mcp.state._ceremony_nudge_selectors import (
    _select_learning_injection_candidate as _select_learning_injection_candidate,
)
from trw_mcp.state._ceremony_nudge_selectors import (
    select_contextual_nudge_content as select_contextual_nudge_content,
)
from trw_mcp.state._ceremony_nudge_selectors import (
    select_learning_injection_content as select_learning_injection_content,
)

