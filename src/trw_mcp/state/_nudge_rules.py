"""Nudge decision logic — when to show which nudge, priority, phase checks.

Extracted from ceremony_nudge.py (PRD-CORE-074 FR01; PRD-CORE-084 FR04, FR05).

Bounded context: decision logic. No state I/O, no message text.

All functions receive CeremonyState (or NudgeContext) as arguments and return
decisions (step names, booleans, tuples). They never read or write the filesystem.
"""

from __future__ import annotations

from trw_mcp.state._nudge_state import _STEPS, CeremonyState, NudgeContext
from trw_mcp.state._nudge_state import _step_complete as _step_complete  # re-export

# Phase-to-applicable-steps mapping (FR04, PRD-CORE-084)
_PHASE_APPLICABLE_STEPS: dict[str, tuple[str, ...]] = {
    "early": ("session_start", "checkpoint"),
    "implement": ("session_start", "checkpoint"),
    "validate": ("session_start", "checkpoint", "build_check"),
    "review": ("session_start", "checkpoint", "build_check", "review"),
    "deliver": _STEPS,
    "done": _STEPS,
}


# ---------------------------------------------------------------------------
# Priority-based step selection
# ---------------------------------------------------------------------------


def _highest_priority_pending_step(state: CeremonyState) -> str | None:
    """Return the highest-priority pending step name, or None if all done."""
    # Priority 1: session_start
    if not state.session_started:
        return "session_start"

    # Priority 2: checkpoint (if files modified > 3 OR no checkpoint in session)
    needs_checkpoint = state.files_modified_since_checkpoint > 3 or state.checkpoint_count == 0
    if needs_checkpoint:
        return "checkpoint"

    # Priority 3: build_check (if phase >= validate and not run)
    if state.phase in ("validate", "review", "deliver", "done") and state.build_check_result != "passed":
        return "build_check"

    # Priority 4: review (if phase >= review and not called)
    if state.phase in ("review", "deliver", "done") and not state.review_called:
        return "review"

    # Priority 5: deliver (if phase >= deliver)
    if state.phase in ("deliver", "done") and not state.deliver_called:
        return "deliver"

    return None


# ---------------------------------------------------------------------------
# FR04 (PRD-CORE-084): Next-two-steps projection
# ---------------------------------------------------------------------------


def _next_two_steps(state: CeremonyState) -> tuple[str | None, str | None]:
    """Return the next two incomplete ceremony steps applicable to the current phase.

    Returns (next, then) or (next, None) or (None, None).
    """
    applicable = _PHASE_APPLICABLE_STEPS.get(state.phase, _STEPS)
    pending: list[str] = []
    for step in applicable:
        if not _step_complete(step, state) and len(pending) < 2:
            pending.append(step)
    nxt = pending[0] if len(pending) >= 1 else None
    then = pending[1] if len(pending) >= 2 else None
    return nxt, then


# ---------------------------------------------------------------------------
# FR05 (PRD-CORE-084): Phase reversion active prompting
# ---------------------------------------------------------------------------


def _reversion_prompt(context: NudgeContext | None, state: CeremonyState) -> str | None:
    """Return a phase-reversion prompt if conditions warrant it.

    Returns None when no reversion is appropriate, or when the caller is a subagent.
    """
    if context is None:
        return None

    # Subagents should not receive reversion prompts
    if context.is_subagent:
        return None

    # Trigger 1: Build failure
    if context.build_passed is False:
        return "If failures reveal a design flaw, revert to PLAN. If implementation bugs, fix in-phase."

    # Trigger 2: P0 findings from review
    if context.review_p0_count > 0:
        return "If P0 requires architectural change, revert to PLAN. If isolated fix, remediate and re-validate."

    # Trigger 3: Scope creep (many checkpoint nudges + many files modified)
    checkpoint_nudges = state.nudge_counts.get("checkpoint", 0)
    if checkpoint_nudges >= 5 and state.files_modified_since_checkpoint > 10:
        return (
            "Scope may have grown beyond the current plan. "
            "Reverting to PLAN to reassess is a quality signal, not a failure."
        )

    return None


# ---------------------------------------------------------------------------
# FR12: Local model detection (PRD-CORE-074)
# ---------------------------------------------------------------------------


def is_local_model(model_id: str) -> bool:
    """Detect if a model ID indicates a local model.

    Local model indicators:
    - Starts with "ollama/"
    - Starts with "local/"
    - Contains "localhost"
    """
    model_lower = model_id.lower()
    return model_lower.startswith(("ollama/", "local/")) or "localhost" in model_lower
