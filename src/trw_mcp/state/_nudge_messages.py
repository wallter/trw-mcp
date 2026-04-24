"""Nudge message templates and formatting.

Extracted from ceremony_nudge.py (PRD-CORE-074 FR01, FR02, FR03; PRD-CORE-084 FR03, FR06).

Bounded context: message text generation. No state I/O, no decision logic.

All functions receive pre-computed state/urgency/context as arguments.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import structlog

logger = structlog.get_logger(__name__)

from trw_mcp.state._nudge_state import _STEPS as _STEPS
from trw_mcp.state._nudge_state import CeremonyState, NudgeContext, ToolName, _step_complete

if TYPE_CHECKING:
    from trw_mcp.models.config._client_profile import ClientProfile


# ---------------------------------------------------------------------------
# Profile-aware template substitution (PRD-CORE-149 FR02, FR03, FR12)
# ---------------------------------------------------------------------------


def format_nudge(template: str, profile: "ClientProfile | None") -> str:
    """Substitute profile-derived placeholders into a nudge template.

    PRD-CORE-149 FR02/FR03: supports ``{client_display_name}`` and
    ``{client_config_dir}`` placeholders so nudge prose can adapt to any
    registered client without hardcoding the claude-code identifiers.

    PRD-CORE-149 FR12: when ``profile`` is ``None`` or lacks a required field
    (empty ``display_name`` / unresolvable ``config_dir``), the formatter falls
    back to the safest identifier available (``profile.client_id`` for display
    name; ``.trw`` for config dir) and emits ``structlog.warn('profile.fallback')``
    rather than raising.

    If the template contains no substitution braces, the original string is
    returned unchanged -- making this a zero-cost wrap for already-literal
    messages.
    """
    # Fast path: no placeholders -> nothing to substitute (covers every current
    # message in this module post-2026-03 cleanup -- see PRD-CORE-149 §FR02).
    if "{client_display_name}" not in template and "{client_config_dir}" not in template:
        return template

    display_name = ""
    config_dir = ""
    client_id = "<unknown>"
    if profile is not None:
        client_id = profile.client_id
        display_name = profile.display_name or ""
        try:
            config_dir = profile.config_dir
        except Exception:  # justified: FR12 fallback — any attr/property failure falls back
            config_dir = ""

    missing: list[str] = []
    if not display_name:
        display_name = client_id
        missing.append("display_name")
    if not config_dir:
        config_dir = ".trw"
        missing.append("config_dir")

    if missing:
        logger.warning(
            "profile.fallback",
            missing_field=",".join(missing),
            client_id=client_id,
        )

    try:
        return template.format(
            client_display_name=display_name,
            client_config_dir=config_dir,
        )
    except (KeyError, IndexError):
        logger.warning(
            "profile.fallback",
            missing_field="format_error",
            client_id=client_id,
        )
        return template

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


def _select_nudge_message(
    step: str,
    state: CeremonyState,
    available_learnings: int,
    profile: "ClientProfile | None" = None,
) -> str:
    """Select the value-expressing static nudge message for the given step.

    Messages follow the value-expression template (FR02):
      fact -> value -> consequence -> effort framing.
    No prescriptive language ("MUST", "CRITICAL", etc.) or decision language
    in these static messages. (Context-reactive messages in
    ``_context_reactive_message`` MAY use prescriptive language per
    PRD-CORE-084 FR06.)

    Progressive urgency (FR03): messages grow more specific based on nudge_counts[step].

    PRD-CORE-149 FR03: when ``profile`` is provided, the returned template is
    piped through :func:`format_nudge` so ``{client_display_name}`` /
    ``{client_config_dir}`` placeholders resolve to the active client's
    identity. When ``profile`` is ``None``, literals pass through unchanged
    (fast path in :func:`format_nudge`).
    """
    template = _select_nudge_template(step, state, available_learnings)
    return format_nudge(template, profile)


def _select_nudge_template(step: str, state: CeremonyState, available_learnings: int) -> str:
    """Return the raw (pre-substitution) template for ``step`` at current urgency.

    PRD-CORE-149 FR03: split out so profile-aware substitution happens in a
    single place (``_select_nudge_message``) while the template bodies remain
    focused on ceremony semantics.
    """
    urgency = _compute_urgency(state, step)

    if step == "session_start":
        n = available_learnings
        if n > 0:
            return _select_message_by_urgency(
                urgency,
                low=(
                    f"\u26a1 {n} prior learnings load in 1s for {{client_display_name}} — "
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
                logger.debug(
                    "checkpoint_elapsed_parse_skipped", exc_info=True
                )  # justified: fail-open, elapsed display is cosmetic
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
                "\u26a1 Verification not run yet — "
                "tests + type-check catches integration issues before delivery. "
                "trw_build_check() runs the full gate."
            ),
            medium=(
                "\u26a1 Verification not run — "
                "type errors and test failures are undetected; delivery ships them as-is. "
                "trw_build_check() runs the full gate."
            ),
            high=(
                "\u26a1 Verification not run — "
                "integration issues delivered without verification stay embedded in the result. "
                "trw_build_check() catches them in under 2 minutes."
            ),
        )

    if step == "review":
        return _select_message_by_urgency(
            urgency,
            low=("\u26a1 Independent review not yet called — trw_review() catches spec drift that passing tests miss."),
            medium=(
                "\u26a1 Review skipped — delivering without review ships unverified changes. "
                "trw_review() takes under 1 minute."
            ),
            high=(
                "\u26a1 Independent review has not been called — "
                "spec drift and architectural issues go undetected. "
                "trw_review() is required before trw_deliver()."
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
            low=("\u26a1 Session complete for {client_display_name} — trw_deliver() persists the run and any learnings for future sessions."),
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
    ceremony_mode: str = "full",
) -> str | None:
    """Select context-reactive nudge message based on tool result.

    Returns None for unknown tool_name (triggers fallback to static messages).
    FR06: urgency scales language from informational to directive.

    Args:
        context: Tool call context with tool_name and result info.
        state: Current ceremony state.
        urgency: Urgency level ('low', 'medium', 'high').
        ceremony_mode: Client ceremony mode ('full' or 'light').
            Light-mode clients get shorter messages without FRAMEWORK.md references.

    Note: Unlike static urgency-tier messages (PRD-CORE-074), context-reactive
    messages MAY use prescriptive language (MUST, SHALL, SHOULD) at medium
    and high urgency levels per PRD-CORE-084 FR06.
    """
    tool = context.tool_name
    if tool == ToolName.BUILD_CHECK:
        return _build_check_message(context, urgency)
    if tool == ToolName.REVIEW:
        return _review_message(context)
    if tool == ToolName.CHECKPOINT:
        return _checkpoint_message()
    if tool == ToolName.LEARN:
        return _learn_message(ceremony_mode)
    if tool == ToolName.SESSION_START:
        return _session_start_message(ceremony_mode)
    if tool == ToolName.DELIVER:
        return _deliver_message(state)
    if tool == ToolName.INIT:
        return "Run bootstrapped. NEXT: Begin the work. THEN: trw_checkpoint() at first milestone."
    if tool == ToolName.RECALL:
        return "Learnings recalled. Review them for relevant patterns before proceeding."
    if tool == ToolName.STATUS:
        return "Run status loaded. Resume from last checkpoint rather than re-implementing."
    if tool == ToolName.PRD_CREATE:
        return "PRD created. NEXT: trw_prd_validate() — catches ambiguity and gaps before the work starts."
    if tool == ToolName.PRD_VALIDATE:
        return "PRD validated. NEXT: trw_init() to bootstrap the run. THEN: begin the work."
    return None


def _build_check_message(context: NudgeContext, urgency: str) -> str | None:
    """Return the context-reactive message for build-check results."""
    if context.build_passed is False:
        return (
            "Build failed. If failures reveal a design flaw, revert to PLAN "
            "— fixing a plan costs less than patching broken code. "
            "If the work has execution bugs, fix them in-phase and re-run."
        )
    if context.build_passed is not True:
        return None

    suffix = "independent verification catches spec drift that passing tests miss. THEN: trw_deliver()"
    if urgency == "high":
        return f"NEXT: trw_review() SHOULD be performed — {suffix}"
    if urgency == "medium":
        return f"NEXT: trw_review() is recommended — {suffix}"
    return f"NEXT: trw_review() — {suffix}"


def _review_message(context: NudgeContext) -> str:
    """Return the context-reactive message for review outcomes."""
    if context.review_p0_count > 0:
        return (
            "P0 findings detected. A separate agent MUST remediate "
            "— the reviewer SHALL NOT fix its own findings. "
            "THEN: re-validate with trw_build_check()."
        )
    return "NEXT: trw_deliver() — persist learnings and artifacts for future sessions."


def _checkpoint_message() -> str:
    """Return the checkpoint reminder message."""
    return (
        "Progress saved. Quick check: have you recorded what you discovered so far? "
        "trw_learn() persists your insights across sessions \u2014 "
        "even a one-line root cause compounds for future agents."
    )


def _learn_message(ceremony_mode: str) -> str:
    """Return the learning follow-up message."""
    if ceremony_mode == "light":
        return "Learning persisted. Continue the work, then call trw_deliver() when done."
    return (
        "Learning persisted. NEXT: trw_checkpoint() at next milestone. "
        "THEN: trw_build_check() when the work is complete."
    )


def _session_start_message(ceremony_mode: str) -> str:
    """Return the session-start guidance message."""
    if ceremony_mode == "light":
        return (
            "What's your approach? State it before editing files. "
            "THEN: trw_init() for new work or trw_status() to resume."
        )
    return (
        "NEXT: Read FRAMEWORK.md (phases, gates, reversion rules). "
        "What's your approach? State it before editing files. "
        "THEN: trw_init() for new work or trw_status() to resume."
    )


def _deliver_message(state: CeremonyState) -> str:
    """Return the delivery completion message."""
    n = state.learnings_this_session
    if n > 0:
        return f"Session complete. {n} discovery/discoveries persisted for future sessions."
    return "Session complete. 0 learnings recorded \u2014 future agents start without your insights."


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
# Done/Next/Then status line (PRD-CORE-125 FR04)
# ---------------------------------------------------------------------------

# WHY rationale for Next/Then lines (short, consequence-oriented)
_DONE_NEXT_RATIONALE: dict[str, str] = {
    "session_start": "loads prior learnings",
    "checkpoint": "saves progress against context loss",
    "build_check": "catches integration issues before delivery",
    "review": "independent verification catches spec drift",
    "deliver": "persists your learnings for future agents",
}


def _build_done_next_then_status(state: CeremonyState) -> str:
    """Build a Done/Next/Then status line for full-mode clients.

    PRD-CORE-125 FR04: Replaces checkmark format with a more parseable format.
    Format:
        Done: session_start, learn(1)
        Next: checkpoint \u2014 saves progress against context loss
        Then: deliver \u2014 persists your learnings for future agents

    Returns a string under 200 characters.
    """
    # Build "Done" items
    done_items: list[str] = []
    if _step_complete("session_start", state):
        done_items.append("session_start")
    if _step_complete("checkpoint", state):
        done_items.append("checkpoint")
    if state.learnings_this_session > 0:
        done_items.append(f"learn({state.learnings_this_session})")
    if _step_complete("build_check", state):
        done_items.append("build_check")
    if _step_complete("review", state):
        done_items.append("review")
    if _step_complete("deliver", state):
        done_items.append("deliver")

    # Find next two incomplete steps in order
    pending: list[str] = []
    for step in _STEPS:
        if not _step_complete(step, state):
            pending.append(step)
        if len(pending) >= 2:
            break

    lines: list[str] = []
    if done_items:
        lines.append(f"Done: {', '.join(done_items)}")
    if len(pending) >= 1:
        rationale = _DONE_NEXT_RATIONALE.get(pending[0], "")
        lines.append(f"Next: {pending[0]} \u2014 {rationale}")
    if len(pending) >= 2:
        rationale = _DONE_NEXT_RATIONALE.get(pending[1], "")
        lines.append(f"Then: {pending[1]} \u2014 {rationale}")

    result = "\n".join(lines)
    # Enforce 200-char budget
    if len(result) > 200:
        result = result[:197] + "..."
    return result


def _build_done_next_then_status_light(state: CeremonyState) -> str:
    """Build a compact Done/Next/Then status line for light-mode clients.

    PRD-CORE-125 FR04: Single pipe-separated line under 100 characters.
    Format: Done: session_start | Next: learn \u2014 record what you found | Then: deliver
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
        parts.append(f"Next: {pending[0]} \u2014 {short_rationale}")
    if len(pending) >= 2:
        parts.append(f"Then: {pending[1]}")

    result = " | ".join(parts)
    # Enforce 100-char budget
    if len(result) > 100:
        result = result[:97] + "..."
    return result


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

    Priority: status_line (always) > reactive_msg (budget-checked) >
    next_then (if budget allows) > reversion (if budget allows).

    PRD-CORE-120-FR02: Hard truncation enforced. If the final assembled
    string exceeds the budget, it is truncated to (budget - 12) characters
    with `` [truncated]`` appended. The status_line is never truncated --
    if it alone exceeds budget, it is returned as-is.
    """
    _TRUNCATION_MARKER = " [truncated]"
    _MARKER_LEN = len(_TRUNCATION_MARKER)  # 12

    components: list[str] = [status_line]

    # Check remaining budget before adding reactive_msg
    if reactive_msg:
        current_len = len(status_line)
        remaining = budget - current_len - 1  # -1 for the newline separator
        if remaining > 0:
            if len(reactive_msg) <= remaining:
                components.append(reactive_msg)
            else:
                # Truncate reactive_msg to fit within budget
                if remaining > _MARKER_LEN:
                    components.append(reactive_msg[: remaining - _MARKER_LEN] + _TRUNCATION_MARKER)
                else:
                    components.append(reactive_msg[:remaining])

    current = "\n".join(components)
    if next_then and len(current) + len(next_then) + 1 <= budget:
        components.append(next_then)
        current = "\n".join(components)

    if reversion and len(current) + len(reversion) + 1 <= budget:
        components.append(reversion)

    result = "\n".join(components)

    # PRD-CORE-120-FR02: Hard truncation at budget limit
    if len(result) > budget:
        # Status line alone exceeds budget — return as-is (never truncate status)
        if len(status_line) >= budget:
            return status_line
        # Truncate the full result with indicator
        truncated = result[: budget - _MARKER_LEN] + _TRUNCATION_MARKER
        logger.debug(
            "nudge_truncated",
            pre_truncation_len=len(result),
            budget=budget,
            chars_removed=len(result) - len(truncated),
        )
        return truncated

    return result
