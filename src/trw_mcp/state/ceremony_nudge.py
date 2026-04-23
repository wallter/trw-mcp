"""Legacy ceremony-nudge compatibility surface.

Live tool paths are isolated behind ``ceremony_progress`` and
``tools._ceremony_status``. This module remains available for offline/legacy
nudge callers and re-exports the legacy APIs without owning live wiring.
"""

from __future__ import annotations

from pathlib import Path

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


def compute_nudge_silent_flow(
    state: CeremonyState,
    trw_dir: Path,
    context: NudgeContext | None = None,
) -> str:
    """Render rich contextual nudge only if in distress; otherwise pure silence.

    Iter-25 'Zero-Tax' variant for high-performing models. Eliminates token overhead
    unless intervention is clinically necessary.
    """

    try:
        if _is_agent_in_distress(state):
            return compute_nudge_contextual(state, trw_dir, context=context)

        return ""
    except Exception:  # justified: fail-open per NFR02
        logger.debug("compute_nudge_silent_flow_failed", exc_info=True)
        return ""


def compute_nudge_stepback(
    state: CeremonyState,
    trw_dir: Path,
    context: NudgeContext | None = None,
) -> str:
    """Render a 'Step-Back' rescue nudge if distress is detected.

    Prompts the agent to hypothesize and identify core principles when stalled.
    """

    try:
        if _is_agent_in_distress(state):
            status = _build_minimal_status_line(state)
            msg = (
                f"{_MINIMAL_HEADER}\n{status}\n"
                "\u26a0 STOP. Step back. Identify the core principle or problem category. "
                "Form a hypothesis before your next tool call."
            )
            return msg

        return compute_nudge_contextual(state, trw_dir, context=context)
    except Exception:  # justified: fail-open per NFR02
        logger.debug("compute_nudge_stepback_failed", exc_info=True)
        return compute_nudge_minimal(state)


def compute_nudge_cod(state: CeremonyState) -> str:
    """Render a 'Chain of Draft' (CoD) shorthand nudge."""

    try:
        status = _build_done_next_then_status_light(state)
        return f"{_MINIMAL_HEADER}\n{status}"
    except Exception:  # justified: fail-open per NFR02
        logger.debug("compute_nudge_cod_failed", exc_info=True)
        return compute_nudge_minimal(state)


def compute_nudge_anchor(state: CeremonyState, trw_dir: Path, context: NudgeContext | None = None) -> str:
    """Render a nudge wrapped in high-signal XML tags to anchor attention."""

    try:
        content = compute_nudge_contextual(state, trw_dir, context=context)
        return f"<TRW_SESSION_ANCHOR>\n{content}\n</TRW_SESSION_ANCHOR>"
    except Exception:  # justified: fail-open per NFR02
        logger.debug("compute_nudge_anchor_failed", exc_info=True)
        return compute_nudge_minimal(state)


def compute_nudge_negative(state: CeremonyState) -> str:
    """Render a nudge using negative constraints (anti-patterns)."""

    try:
        status = _build_minimal_status_line(state)
        pending = _highest_priority_pending_step(state)

        if pending == "session_start":
            msg = "Do NOT proceed with edits until trw_session_start() is called."
        elif pending == "checkpoint":
            msg = "Do NOT risk context compaction; call trw_checkpoint() now."
        elif pending == "build_check":
            msg = "Do NOT deliver unverified code; run trw_build_check() first."
        elif pending == "review":
            msg = "Do NOT skip independent review; call trw_review() before delivery."
        elif pending == "deliver":
            msg = "Do NOT end this session without calling trw_deliver() to persist learnings."
        else:
            msg = "Do NOT violate ceremony requirements."

        return f"{_MINIMAL_HEADER}\n{status}\n\u26a0 {msg}"
    except Exception:  # justified: fail-open per NFR02
        logger.debug("compute_nudge_negative_failed", exc_info=True)
        return compute_nudge_minimal(state)


def compute_nudge_governance(state: CeremonyState, available_learnings: int = 0) -> str:
    """Render a governance-only nudge (status line only, no prompt text)."""

    try:
        status_line = _build_minimal_status_line(state)
        return f"{_MINIMAL_HEADER}\n{status_line}"
    except Exception:  # justified: fail-open per NFR02
        logger.debug("compute_nudge_governance_failed", exc_info=True)
        return f"{_MINIMAL_HEADER}\n? start | ? deliver"


def _select_learning_injection_candidate(
    state: CeremonyState,
    trw_dir: Path,
    *,
    skip_phase_duplicates: bool = False,
) -> tuple[dict[str, object] | None, str | None]:
    """Return the selected learning entry and active target filename."""

    from trw_mcp.state.learning_injection import infer_domain_tags
    from trw_mcp.state.memory_adapter import recall_learnings
    from trw_mcp.state.recall_context import build_recall_context

    # PRD-CORE-146 follow-up: ``build_recall_context`` was relocated from
    # ``tools/_recall_impl`` into ``state/recall_context`` so this caller no
    # longer needs an importlib workaround to dodge the state→tools layer lint.
    recall_context = build_recall_context(trw_dir, "*")
    modified_files_raw = getattr(recall_context, "modified_files", []) if recall_context is not None else []
    modified_files = [str(path).strip() for path in modified_files_raw if str(path).strip()]
    if not modified_files:
        return None, None

    target_path = Path(modified_files[0])
    target_label = target_path.name
    query = " ".join(
        part
        for part in (
            target_path.stem,
            target_path.parent.name,
        )
        if part and part != "."
    ).strip()
    domain_tags = sorted(infer_domain_tags([target_path.as_posix()]))
    attempts = (
        (query or target_label, domain_tags or None),
        ("*", domain_tags or None),
        (query or target_label, None),
    )

    selected_learning: dict[str, object] | None = None
    seen_ids: set[str] = set()

    for attempt_query, attempt_tags in attempts:
        learnings = recall_learnings(
            trw_dir,
            query=attempt_query,
            tags=attempt_tags,
            min_impact=0.5,
            max_results=8,
            compact=False,
        )
        for learning in learnings:
            learning_id = str(learning.get("id", "")).strip()
            summary = str(learning.get("summary", "")).strip()
            if not learning_id or not summary or learning_id in seen_ids:
                continue
            seen_ids.add(learning_id)
            if skip_phase_duplicates and learning_id in state.nudge_history:
                phases_shown = state.nudge_history[learning_id].get("phases_shown", [])
                if state.phase in phases_shown:
                    try:
                        from trw_mcp.state._nudge_rules import _resolve_client_id

                        logger.debug(
                            "nudge_skipped",
                            reason="phase_dedup",
                            pool="learning_injection",
                            learning_id=learning_id,
                            client_id=_resolve_client_id(),
                        )
                    except Exception:  # justified: fail-open per NFR02
                        pass
                    continue
            selected_learning = learning
            break
        if selected_learning is not None:
            break

    return selected_learning, target_label


def _contextual_next_step_message(
    state: CeremonyState,
    *,
    target_label: str | None,
    context: NudgeContext | None = None,
) -> str:
    """Build the action-oriented line for the contextual messenger."""

    pending = _highest_priority_pending_step(state)
    urgency = _compute_urgency(state, pending or "checkpoint")

    if context is not None:
        reactive = _context_reactive_message(context, state, urgency=urgency)
        if reactive:
            return reactive

    if pending == "session_start":
        return "NEXT: trw_session_start() — loads prior learnings and run state before more edits."
    if pending == "checkpoint":
        anchor = f" once {target_label} is stable" if target_label else " at the next stable milestone"
        return f"NEXT: trw_checkpoint(){anchor} — {_STEP_RATIONALE['checkpoint']}."
    if pending == "build_check":
        return f"NEXT: trw_build_check() — {_STEP_RATIONALE['build_check']} before review or deliver."
    if pending == "review":
        return f"NEXT: trw_review() — {_STEP_RATIONALE['review']} before deliver."
    if pending == "deliver":
        if state.learnings_this_session > 0:
            return (
                "NEXT: trw_deliver() — "
                f"{_STEP_RATIONALE['deliver']} and preserve {state.learnings_this_session} learning(s)."
            )
        return f"NEXT: trw_deliver() — {_STEP_RATIONALE['deliver']}."

    target_phrase = f" on {target_label}" if target_label else ""
    phase = state.phase or "current"
    return f"NEXT: continue the {phase} work{target_phrase}; trw_checkpoint() at the next stable milestone."


def select_contextual_nudge_content(
    state: CeremonyState,
    trw_dir: Path,
    *,
    context: NudgeContext | None = None,
    skip_phase_duplicates: bool = False,
    include_learning_caution: bool = True,
) -> tuple[str | None, str | None, str | None]:
    """Return contextual nudge content, optional learning id, and target file."""

    try:
        selected_learning, target_label = _select_learning_injection_candidate(
            state,
            trw_dir,
            skip_phase_duplicates=skip_phase_duplicates,
        )
        action_line = _contextual_next_step_message(state, target_label=target_label, context=context)
        status_line = _build_minimal_status_line(state)
        lines = [_MINIMAL_HEADER, status_line, action_line]

        learning_id: str | None = None
        if selected_learning is not None and include_learning_caution:
            learning_id = str(selected_learning.get("id", "")).strip() or None
            raw_caution = str(
                selected_learning.get("nudge_line") or selected_learning.get("summary") or ""
            ).strip()
            if raw_caution:
                clipped_caution = raw_caution[:120] + ("..." if len(raw_caution) > 120 else "")
                target_phrase = f" for {target_label}" if target_label else ""
                source_suffix = f" Source: {learning_id}." if learning_id else ""
                lines.append(f"Watch-out{target_phrase}: {clipped_caution}.{source_suffix}".replace("..", "."))

        rendered = "\n".join(line for line in lines if line)
        clipped = rendered if len(rendered) <= 400 else rendered[:397] + "..."
        return clipped, learning_id, target_label
    except Exception:  # justified: fail-open -- recall issues must not break ceremony status
        logger.debug("select_contextual_nudge_content_failed", exc_info=True)
        return None, None, None


def select_learning_injection_content(
    state: CeremonyState,
    trw_dir: Path,
    *,
    skip_phase_duplicates: bool = False,
) -> tuple[str | None, str | None, str | None]:
    """Return rendered content, learning id, and target file for the injection branch."""

    try:
        selected_learning, target_label = _select_learning_injection_candidate(
            state,
            trw_dir,
            skip_phase_duplicates=skip_phase_duplicates,
        )
        if selected_learning is None:
            return None, None, target_label

        learning_id = str(selected_learning.get("id", "")).strip()
        summary = str(selected_learning.get("summary", "")).strip()
        clipped_summary = summary[:120] + ("..." if len(summary) > 120 else "")
        status_line = _build_minimal_status_line(state)
        message = (
            f"{_MINIMAL_HEADER}\n"
            f"{status_line}\n"
            f"[!] Past learning on {target_label}: {clipped_summary}. Source: {learning_id}."
        )
        rendered = message if len(message) <= 400 else message[:397] + "..."
        return rendered, learning_id, target_label
    except Exception:  # justified: fail-open -- recall issues must not break ceremony status
        logger.debug("select_learning_injection_content_failed", exc_info=True)
        return None, None, None
