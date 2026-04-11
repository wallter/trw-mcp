"""Nudge decision logic — when to show which nudge, priority, phase checks.

Extracted from ceremony_nudge.py (PRD-CORE-074 FR01; PRD-CORE-084 FR04, FR05).

Bounded context: decision logic. No state I/O, no message text.

All functions receive CeremonyState (or NudgeContext) as arguments and return
decisions (step names, booleans, tuples). They never read or write the filesystem.
"""

from __future__ import annotations

import random

import structlog

from trw_mcp.models.config._client_profile import NudgePoolWeights
from trw_mcp.state._nudge_state import _STEPS, CeremonyState, NudgeContext
from trw_mcp.state._nudge_state import _step_complete as _step_complete  # re-export

logger = structlog.get_logger(__name__)
_RNG = random.SystemRandom()

# Phase-to-applicable-steps mapping (FR04, PRD-CORE-084)
#
# PRD-CORE-120-FR04: Rationale for each phase's ceremony step selection.
#
# Each phase only nudges for ceremony steps that are actionable at that point.
# Steps are cumulative -- later phases include all earlier steps plus new ones:
#
#   early:      session_start, checkpoint
#               (only startup and progress-saving matter before real work begins)
#   implement:  session_start, checkpoint
#               (same as early -- build_check/review/deliver are premature during coding)
#   validate:   session_start, checkpoint, build_check
#               (build_check becomes actionable -- tests and type-checks should run now)
#   review:     session_start, checkpoint, build_check, review
#               (review becomes actionable -- independent verification of completed work)
#   deliver:    all steps (session_start, checkpoint, build_check, review, deliver)
#               (deliver becomes actionable -- persist learnings and close the session)
#   done:       all steps
#               (same as deliver -- any incomplete step should still be nudged)
#
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

    # Priority 2: checkpoint (if files modified > 10 OR no checkpoint in session)
    # PRD-CORE-129: Raised threshold from 3 to 10 to reduce checkpoint dominance.
    # Suppress checkpoint nudges in validate/review/deliver phases where the agent
    # should focus on verification and delivery, not mid-work saving.
    _checkpoint_suppressed_phases = ("validate", "review", "deliver", "done")
    if state.phase not in _checkpoint_suppressed_phases:
        needs_checkpoint = state.files_modified_since_checkpoint > 10 or state.checkpoint_count == 0
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
# PRD-CORE-103: Learning nudge deduplication
# ---------------------------------------------------------------------------


def select_nudge_learning(
    state: CeremonyState,
    candidates: list[dict[str, object]],
    current_phase: str,
    *,
    bandit: object | None = None,
    previous_phase: str = "",
    client_class: str = "full_mode",
    burst_items: list[dict[str, object]] | None = None,
) -> tuple[dict[str, object] | None, bool]:
    """Select the best learning for nudge display with deduplication.

    When *bandit* is provided (a ``BanditSelector`` instance), delegates to
    the bandit-based selection path (PRD-CORE-105 FR03/FR04/FR06) which
    supports tiered withholding and phase-transition bursts.

    Without *bandit*, falls back to the original deterministic ranking.

    Filters candidates by nudge eligibility (not shown in current phase),
    then returns the top remaining candidate. If all candidates are
    already shown, falls back to the least-recently-shown candidate.

    PRD-CORE-105 P0: When *burst_items* is provided and the bandit detects
    a phase transition, extra burst items (beyond the first) are appended
    to the list so the caller can render them.

    Args:
        state: Current ceremony state with nudge_history.
        candidates: Ranked learning dicts (best first).
        current_phase: Current ceremony phase.
        bandit: Optional BanditSelector for bandit-based selection.
        previous_phase: Previous phase for transition detection.
        client_class: Client class for withholding rates.
        burst_items: Optional mutable list to receive additional burst
            selections during phase transitions. The primary selection
            is still returned normally; extra items go here.

    Returns:
        Tuple of (selected_learning_dict_or_None, is_fallback).
        is_fallback is True if we fell back to least-recently-shown.
    """
    from trw_mcp.state._nudge_state import is_nudge_eligible

    # --- Bandit-based selection path (PRD-CORE-105) ---
    # Intelligence code (WithholdingPolicy, select_nudge_learning_bandit) was
    # extracted to the backend in PRD-INFRA-052 and removed from trw-mcp in
    # PRD-INFRA-054.  The bandit parameter is accepted for API compatibility
    # but the selection now falls through to the deterministic ranking path.
    # When a backend is connected, cached intelligence enriches scoring via
    # intel_boost (PRD-INFRA-053) rather than local bandit computation.

    # --- Deterministic ranking path (original behavior) ---

    # Filter to eligible candidates
    eligible = [
        c for c in candidates if is_nudge_eligible(state, str(c.get("id", "")), current_phase)
    ]

    if eligible:
        return eligible[0], False

    # Fallback: least recently shown candidate
    if candidates:

        def _last_shown_turn(c: dict[str, object]) -> int:
            lid = str(c.get("id", ""))
            if lid in state.nudge_history:
                return state.nudge_history[lid]["last_shown_turn"]
            return 0

        fallback = min(candidates, key=_last_shown_turn)
        return fallback, True

    return None, False


# ---------------------------------------------------------------------------
# FR12: Local model detection (PRD-CORE-074)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# PRD-CORE-129: Pool-based nudge selection
# ---------------------------------------------------------------------------


def is_pool_in_cooldown(state: CeremonyState, pool: str) -> bool:
    """Check if a pool is currently in cooldown.

    A pool is in cooldown when the tool_call_counter has not yet reached
    the cooldown_until value for that pool.
    """
    cooldown_until = state.pool_cooldown_until.get(pool, 0)
    return state.tool_call_counter < cooldown_until


def apply_pool_cooldown(
    state: CeremonyState,
    pool: str,
    cooldown_after: int,
    cooldown_calls: int,
) -> bool:
    """Check if pool should enter cooldown, apply if so.

    Returns True if cooldown was activated. Resets the ignore count
    for the pool when cooldown is applied.
    """
    ignores = state.pool_ignore_counts.get(pool, 0)
    if cooldown_after > 0 and ignores >= cooldown_after:
        state.pool_cooldown_until[pool] = state.tool_call_counter + cooldown_calls
        state.pool_ignore_counts[pool] = 0
        return True
    return False


def _select_nudge_pool(
    state: CeremonyState,
    weights: NudgePoolWeights,
    context: NudgeContext | None = None,
    cooldown_after: int = 3,
    cooldown_calls: int = 10,
) -> str | None:
    """Select nudge pool via weighted random with cooldown filtering.

    Returns pool name ("workflow", "learnings", "ceremony", "context")
    or None if no eligible pool exists.

    Context pool bypasses weighted selection on build failure or P0.
    """
    # Context pool always wins on build failure or P0
    if context is not None and (context.build_passed is False or context.review_p0_count > 0):
        return "context"

    # Build eligible pool list (not in cooldown, weight > 0)
    pool_weights: dict[str, int] = {
        "workflow": weights.workflow,
        "learnings": weights.learnings,
        "ceremony": weights.ceremony,
        "context": weights.context,
    }
    eligible: dict[str, int] = {}
    for pool, weight in pool_weights.items():
        if weight <= 0:
            continue
        if is_pool_in_cooldown(state, pool):
            logger.debug(
                "nudge_pool_suppressed",
                pool=pool,
                reason="cooldown",
                until=state.pool_cooldown_until.get(pool, 0),
            )
            continue
        eligible[pool] = weight

    if not eligible:
        return None

    # Weighted random selection
    pools = list(eligible.keys())
    w = [eligible[p] for p in pools]
    selected: str = _RNG.choices(pools, weights=w, k=1)[0]

    logger.debug(
        "nudge_pool_selected",
        pool=selected,
        eligible=list(eligible.keys()),
    )

    return selected


def is_local_model(model_id: str) -> bool:
    """Detect if a model ID indicates a local model.

    Local model indicators:
    - Starts with "ollama/"
    - Starts with "local/"
    - Contains "localhost"
    """
    model_lower = model_id.lower()
    return model_lower.startswith(("ollama/", "local/")) or "localhost" in model_lower
