"""Nudge message templates and formatting.

Extracted from ceremony_nudge.py (PRD-CORE-074 FR01, FR02, FR03; PRD-CORE-084 FR03, FR06).

Bounded context: message text generation. No state I/O, no decision logic.

All functions receive pre-computed state/urgency/context as arguments.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

logger = structlog.get_logger(__name__)

from trw_mcp.state._nudge_state import _STEPS as _STEPS
from trw_mcp.state._nudge_state import CeremonyState, NudgeContext, ToolName, _step_complete

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HEADER = "--- TRW Session ---"
_MINIMAL_HEADER = "--- TRW ---"

# Step rationale for next-two-steps projection (FR04, PRD-CORE-084)
_STEP_RATIONALE: dict[str, str] = {
    "session_start": "loads prior learnings and run state",
    "checkpoint": "saves progress against context compaction",
    "build_check": "verifies tests pass and types check",
    "review": "independent verification catches spec drift",
    "deliver": "persists learnings for future sessions",
}


# ---------------------------------------------------------------------------
# Urgency-based message selection
# ---------------------------------------------------------------------------


def _select_message_by_urgency(
    urgency: str,
    low: str,
    medium: str,
    high: str,
) -> str:
    """Select a message template based on urgency level.

    Used internally by _select_nudge_message to DRY message selection.
    """
    if urgency == "high":
        return high
    return medium if urgency == "medium" else low


# ---------------------------------------------------------------------------
# Static nudge messages (PRD-CORE-074 FR01-FR03)
# ---------------------------------------------------------------------------


def _select_nudge_message(step: str, state: CeremonyState, available_learnings: int) -> str:
    """Select the value-expressing static nudge message for the given step.

    Messages follow the value-expression template (FR02):
      fact -> value -> consequence -> effort framing.
    No prescriptive language ("MUST", "CRITICAL", etc.) or decision language
    in these static messages. (Context-reactive messages in
    ``_context_reactive_message`` MAY use prescriptive language per
    PRD-CORE-084 FR06.)

    Progressive urgency (FR03): messages grow more specific based on nudge_counts[step].
    """
    urgency = _compute_urgency(state, step)

    if step == "session_start":
        n = available_learnings
        if n > 0:
            return _select_message_by_urgency(
                urgency,
                low=(
                    f"\u26a1 {n} prior learnings load in 1s — "
                    "past discoveries become active context. "
                    "Call trw_session_start() to begin."
                ),
                medium=(
                    f"\u26a1 {n} prior learnings load in 1s — "
                    f"each skipped loading costs future agents {n} re-discoveries. "
                    "Call trw_session_start() to begin."
                ),
                high=(
                    f"\u26a1 {n} learnings from prior sessions — "
                    f"skipping means re-discovering known gotchas from scratch. "
                    "trw_session_start() takes <1s."
                ),
            )
        return _select_message_by_urgency(
            urgency,
            low=(
                "\u26a1 Session tracking starts with trw_session_start() — "
                "progress, checkpoints, and learnings attach to this run."
            ),
            medium=(
                "\u26a1 Session tracking not started — "
                "progress and learnings won't persist without it. "
                "trw_session_start() wires them to this run."
            ),
            high=(
                "\u26a1 Session tracking not started — "
                "progress, checkpoints, and learnings are unattached to this run. "
                "Without it, this session's work is invisible to future agents. "
                "trw_session_start() takes 1s."
            ),
        )

    if step == "checkpoint":
        n = state.files_modified_since_checkpoint
        # Compute elapsed time since last checkpoint for contextual display
        elapsed = ""
        if state.last_checkpoint_ts:
            try:
                last = datetime.fromisoformat(state.last_checkpoint_ts.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                mins = int((now - last).total_seconds() / 60)
                if mins > 0:
                    elapsed = f", {mins} min ago"
            except (ValueError, TypeError):
                logger.debug("checkpoint_elapsed_parse_skipped", exc_info=True)  # justified: fail-open, elapsed display is cosmetic
        if n > 0:
            return _select_message_by_urgency(
                urgency,
                low=(
                    f"\u26a1 {n} files modified since last checkpoint{elapsed} — "
                    "context compaction would lose this progress. "
                    "trw_checkpoint() saves it in under 2s."
                ),
                medium=(
                    f"\u26a1 {n} files modified since last checkpoint{elapsed} — "
                    f"compaction risk: {n} file(s) of progress lost with no recovery path. "
                    "trw_checkpoint() saves it in under 2s."
                ),
                high=(
                    f"\u26a1 {n} files modified since last checkpoint{elapsed} — "
                    f"context compaction erases all {n} changes permanently. "
                    "trw_checkpoint() saves everything in 2 seconds."
                ),
            )
        return _select_message_by_urgency(
            urgency,
            low=(
                f"\u26a1 No checkpoint in this session yet{elapsed} — "
                "a checkpoint saves state so context compaction can resume here. "
                "trw_checkpoint() takes under 2s."
            ),
            medium=(
                f"\u26a1 No checkpoint yet this session{elapsed} — "
                "context compaction would lose all progress with no recovery path. "
                "trw_checkpoint() takes under 2s."
            ),
            high=(
                f"\u26a1 No checkpoint in this session{elapsed} — "
                "all session progress is unrecoverable if context compacts. "
                "trw_checkpoint() anchors it in 2 seconds."
            ),
        )

    if step == "build_check":
        return _select_message_by_urgency(
            urgency,
            low=(
                "\u26a1 Build check not run yet — "
                "tests + type-check catches integration issues before delivery. "
                "trw_build_check() runs the full gate."
            ),
            medium=(
                "\u26a1 Build check not run — "
                "type errors and test failures are undetected; delivery ships them as-is. "
                "trw_build_check() runs the full gate."
            ),
            high=(
                "\u26a1 Build check not run — "
                "integration issues delivered without verification stay broken in production. "
                "trw_build_check() catches them in under 2 minutes."
            ),
        )

    if step == "deliver":
        n = state.learnings_this_session
        if n > 0:
            return _select_message_by_urgency(
                urgency,
                low=(
                    f"\u26a1 {n} learning(s) recorded this session — "
                    "trw_deliver() persists them for all future sessions. "
                    "Lost if skipped."
                ),
                medium=(
                    f"\u26a1 {n} learning(s) recorded this session — "
                    f"skipping trw_deliver() discards all {n}; future agents lose this context. "
                    "trw_deliver() persists them for all future sessions."
                ),
                high=(
                    f"\u26a1 {n} learning(s) recorded this session — "
                    f"all {n} are lost permanently if the session ends without trw_deliver(). "
                    "Future agents re-learn them from scratch. Takes 2 seconds."
                ),
            )
        return _select_message_by_urgency(
            urgency,
            low=("\u26a1 Session complete — trw_deliver() persists the run and any learnings for future sessions."),
            medium=(
                "\u26a1 Session complete but not delivered — "
                "run record won't persist for future sessions without trw_deliver()."
            ),
            high=(
                "\u26a1 Session complete but not delivered — "
                "the run record and any learnings are unattached until trw_deliver() is called. "
                "Takes 2 seconds."
            ),
        )

    return ""


# ---------------------------------------------------------------------------
# FR03 (PRD-CORE-084): Context-reactive messages
# ---------------------------------------------------------------------------


def _context_reactive_message(
    context: NudgeContext,
    state: CeremonyState,
    urgency: str = "low",
) -> str | None:
    """Select context-reactive nudge message based on tool result.

    Returns None for unknown tool_name (triggers fallback to static messages).
    FR06: urgency scales language from informational to directive.

    Note: Unlike static urgency-tier messages (PRD-CORE-074), context-reactive
    messages MAY use prescriptive language (MUST, SHALL, SHOULD) at medium
    and high urgency levels per PRD-CORE-084 FR06.
    """
    tool = context.tool_name

    if tool == ToolName.BUILD_CHECK:
        if context.build_passed is False:
            return (
                "Build failed. If failures reveal a design flaw, revert to PLAN "
                "— fixing a plan costs less than patching broken code. "
                "If implementation bugs, fix in-phase and re-run."
            )
        if context.build_passed is True:
            if urgency == "high":
                return (
                    "NEXT: trw_review() SHOULD be performed — independent verification "
                    "catches spec drift that passing tests miss. THEN: trw_deliver()"
                )
            if urgency == "medium":
                return (
                    "NEXT: trw_review() is recommended — independent verification "
                    "catches spec drift that passing tests miss. THEN: trw_deliver()"
                )
            return (
                "NEXT: trw_review() — independent verification catches spec drift "
                "that passing tests miss. THEN: trw_deliver()"
            )

    if tool == ToolName.REVIEW:
        if context.review_p0_count > 0:
            return (
                "P0 findings detected. A separate agent MUST remediate "
                "— the reviewer SHALL NOT fix its own findings. "
                "THEN: re-validate with trw_build_check()."
            )
        return "NEXT: trw_deliver() — persist learnings and artifacts for future sessions."

    if tool == ToolName.CHECKPOINT:
        return (
            "Progress saved. Has anything invalidated your current approach? "
            "Reverting to PLAN is cheaper than pushing through a flawed design."
        )

    if tool == ToolName.LEARN:
        return (
            "Learning persisted. NEXT: trw_checkpoint() at next milestone. "
            "THEN: trw_build_check() when implementation complete."
        )

    if tool == ToolName.SESSION_START:
        return (
            "NEXT: Read FRAMEWORK.md (phases, gates, reversion rules). "
            "THEN: trw_init() for new work or trw_status() to resume."
        )

    if tool == ToolName.DELIVER:
        return "Session complete. Learnings persisted for future sessions."

    if tool == ToolName.INIT:
        return "Run bootstrapped. NEXT: Begin implementation. THEN: trw_checkpoint() at first milestone."

    if tool == ToolName.RECALL:
        return "Learnings recalled. Review them for relevant patterns before proceeding."

    return None


# ---------------------------------------------------------------------------
# Status line formatting
# ---------------------------------------------------------------------------


def _build_status_line(state: CeremonyState) -> str:
    """Build the checkmark/cross status line for all ceremony steps.

    Format:  check session_start | cross checkpoint (5 files modified, 12 min since start)
    """
    parts: list[str] = []
    for step in _STEPS:
        mark = "\u2713" if _step_complete(step, state) else "\u2717"
        label = step

        # Add contextual annotation for incomplete steps
        if step == "checkpoint" and not _step_complete(step, state):
            n = state.files_modified_since_checkpoint
            if n > 0:
                label = f"checkpoint ({n} files modified)"
            else:
                label = "checkpoint (no checkpoint yet)"
        elif step == "build_check" and not _step_complete(step, state):
            phase = state.phase
            if phase not in ("validate", "review", "deliver", "done"):
                # Not yet at the phase — show without annotation
                label = "build_check"
        elif step == "deliver" and state.learnings_this_session > 0 and not state.deliver_called:
            label = f"deliver ({state.learnings_this_session} learnings pending)"

        parts.append(f"{mark} {label}")

    return " | ".join(parts)


def _build_minimal_status_line(state: CeremonyState) -> str:
    """Build a compact status line covering only session_start and deliver."""
    start_mark = "\u2713" if state.session_started else "\u2717"
    deliver_mark = "\u2713" if state.deliver_called else "\u2717"
    return f"{start_mark} start | {deliver_mark} deliver"


# ---------------------------------------------------------------------------
# Urgency computation (shared between messages and rules)
# ---------------------------------------------------------------------------


def _compute_urgency(state: CeremonyState, step: str) -> str:
    """Return urgency level based on how many times this step has been nudged.

    Returns: 'low' (0-2 nudges), 'medium' (3-4 nudges), or 'high' (5+ nudges).
    """
    count = state.nudge_counts.get(step, 0)
    if count >= 5:
        return "high"
    return "medium" if count >= 3 else "low"


# ---------------------------------------------------------------------------
# Nudge assembly (FR09, PRD-CORE-084)
# ---------------------------------------------------------------------------


def _assemble_nudge(
    status_line: str,
    reactive_msg: str | None,
    next_then: str | None = None,
    reversion: str | None = None,
    budget: int = 600,
) -> str:
    """Assemble nudge components within a character budget.

    Priority: status_line (always) > reactive_msg (always if present) >
    next_then (if budget allows) > reversion (if budget allows).
    """
    components: list[str] = [status_line]
    if reactive_msg:
        components.append(reactive_msg)

    current = "\n".join(components)
    if next_then and len(current) + len(next_then) + 1 <= budget:
        components.append(next_then)
        current = "\n".join(components)

    if reversion and len(current) + len(reversion) + 1 <= budget:
        components.append(reversion)

    return "\n".join(components)
