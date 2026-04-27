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


def _resolve_client_id() -> str:
    """Best-effort resolution of the active client_profile.client_id.

    Fail-open: returns "" when config cannot be loaded. Used for FR07 skip
    telemetry (PRD-CORE-146 W2B) so `nudge_skipped` events carry the same
    client_id field as `nudge_shown` for correlation.
    """
    try:
        from trw_mcp.models.config import get_config

        return str(getattr(get_config().client_profile, "client_id", "") or "")
    except Exception:  # justified: fail-open per NFR02
        return ""


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

    The public client now always uses deterministic ranking. ``bandit`` and
    the related compatibility parameters remain in the signature so legacy
    callers do not break, but they no longer enable local policy execution.

    Filters candidates by nudge eligibility (not shown in current phase),
    then returns the top remaining candidate. If all candidates are
    already shown, falls back to the least-recently-shown candidate.

    Args:
        state: Current ceremony state with nudge_history.
        candidates: Ranked learning dicts (best first).
        current_phase: Current ceremony phase.
        bandit: Ignored compatibility parameter retained for API stability.
        previous_phase: Previous phase for transition detection.
        client_class: Ignored compatibility parameter retained for API stability.
        burst_items: Optional mutable list retained for API stability. The
            deterministic path never appends burst items.

    Returns:
        Tuple of (selected_learning_dict_or_None, is_fallback).
        is_fallback is True if we fell back to least-recently-shown.
    """
    from trw_mcp.state._nudge_state import is_nudge_eligible

    # --- Deterministic ranking path (original / fallback behavior) ---

    # Filter to eligible candidates
    eligible = [c for c in candidates if is_nudge_eligible(state, str(c.get("id", "")), current_phase)]

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


def is_pool_in_cooldown(
    state: CeremonyState,
    pool: str,
    *,
    wall_clock_max_hours: int | None = None,
) -> bool:
    """Check if a pool is currently in cooldown.

    A pool is in cooldown when the tool_call_counter has not yet reached
    the cooldown_until value for that pool.

    PRD-CORE-144 FR03: when *wall_clock_max_hours* is provided (or resolved
    from config) and more than that many hours have elapsed since the pool
    entered cooldown (tracked in ``pool_cooldown_set_at``), the pool is
    forced out of cooldown on the next evaluation. This prevents the
    primary "learnings" pool from getting stuck indefinitely after a burst
    of pool nudges exceeded the per-pool counter.
    """
    cooldown_until = state.pool_cooldown_until.get(pool, 0)
    if state.tool_call_counter >= cooldown_until:
        return False

    # Wall-clock cap — resolve default from config if not provided.
    if wall_clock_max_hours is None:
        try:
            from trw_mcp.models.config import get_config

            wall_clock_max_hours = int(getattr(get_config(), "nudge_pool_cooldown_wall_clock_max_hours", 24))
        except Exception:  # justified: fail-open — no config means use conservative default
            wall_clock_max_hours = 24

    entered_at_raw = state.pool_cooldown_set_at.get(pool, "")
    if entered_at_raw:
        try:
            import datetime as _dt

            entered_at = _dt.datetime.fromisoformat(entered_at_raw)
            if entered_at.tzinfo is None:
                entered_at = entered_at.replace(tzinfo=_dt.timezone.utc)
            now = _dt.datetime.now(_dt.timezone.utc)
            elapsed_hours = (now - entered_at).total_seconds() / 3600.0
            if elapsed_hours > float(wall_clock_max_hours):
                # Force-expire on this read. Mutates state in place so the
                # next selection pass sees the pool as eligible. The
                # containing nudge tick persists state after pool selection.
                state.pool_cooldown_until[pool] = 0
                state.pool_cooldown_set_at.pop(pool, None)
                state.pool_ignore_counts[pool] = 0
                logger.info(
                    "pool_cooldown_wall_clock_expired",
                    pool=pool,
                    elapsed_hours=round(elapsed_hours, 2),
                    max_hours=wall_clock_max_hours,
                )
                return False
        except (ValueError, TypeError):
            # Corrupt timestamp — drop it, treat as "never cooled" (NFR03).
            state.pool_cooldown_set_at.pop(pool, None)
            return False

    return True


def apply_pool_cooldown(
    state: CeremonyState,
    pool: str,
    cooldown_after: int,
    cooldown_calls: int,
) -> bool:
    """Check if pool should enter cooldown, apply if so.

    Returns True if cooldown was activated. Resets the ignore count
    for the pool when cooldown is applied.

    PRD-CORE-144 FR03: also stamps ``pool_cooldown_set_at[pool]`` with the
    current UTC timestamp so the wall-clock cap can force-expire pools
    that would otherwise stay cooled indefinitely.
    """
    ignores = state.pool_ignore_counts.get(pool, 0)
    if cooldown_after > 0 and ignores >= cooldown_after:
        import datetime as _dt

        # PRD-CORE-146 FR04: nudge_density lever biases cooldown duration.
        # "low" => longer cooldown (fewer nudges), "high" => shorter cooldown
        # (more nudges). None / "medium" preserves legacy behavior.
        effective_cooldown = cooldown_calls
        try:
            from trw_mcp.models.config import get_config

            density = getattr(get_config(), "effective_nudge_density", None)
            if density == "low":
                effective_cooldown = int(cooldown_calls * 2)
            elif density == "high":
                effective_cooldown = max(1, int(cooldown_calls // 2))
        except Exception:  # justified: fail-open — density is a bias, not a gate
            logger.debug("nudge_density_resolve_failed", exc_info=True)

        state.pool_cooldown_until[pool] = state.tool_call_counter + effective_cooldown
        state.pool_cooldown_set_at[pool] = _dt.datetime.now(_dt.timezone.utc).isoformat()
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
            try:
                logger.debug(
                    "nudge_skipped",
                    reason="pool_cooldown",
                    pool=pool,
                    learning_id="",
                    client_id=_resolve_client_id(),
                )
            except Exception:  # justified: fail-open per NFR02
                pass
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
