"""Nudge template selection — extracted from _nudge_messages.py for module-size compliance.

Belongs to the ``_nudge_messages.py`` facade. Re-exported there for back-compat
with `ceremony_nudge.py` (and `_ceremony_status.py`) which import
`_context_reactive_message` via the parent.

Two helpers:
- ``_select_nudge_template`` — large dispatch table mapping (step, state) → template string
- ``_context_reactive_message`` — context-reactive nudge text composer
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from trw_mcp.state._nudge_state import CeremonyState, NudgeContext, ToolName

logger = structlog.get_logger(__name__)

def _select_nudge_template(step: str, state: CeremonyState, available_learnings: int) -> str:
    """Return the raw (pre-substitution) template for ``step`` at current urgency.

    PRD-CORE-149 FR03: split out so profile-aware substitution happens in a
    single place (``_select_nudge_message``) while the template bodies remain
    focused on ceremony semantics.
    """
    # Lazy-import to avoid circular dep: _nudge_messages.py imports from this module.
    from trw_mcp.state._nudge_messages import _compute_urgency, _select_message_by_urgency

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
                "project-native checks catch integration issues before delivery. "
                "Run the repo's validation command, then record it with trw_build_check()."
            ),
            medium=(
                "\u26a1 Verification not run — "
                "test, build, lint, type, or schema failures may be undetected; delivery ships them as-is. "
                "Run project-native validation, then record it with trw_build_check()."
            ),
            high=(
                "\u26a1 Verification not run — "
                "integration issues delivered without verification stay embedded in the result. "
                "Run the narrowest meaningful project-native check now and record the result."
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
            low=(
                "\u26a1 Session complete for {client_display_name} — trw_deliver() persists the run and any learnings for future sessions."
            ),
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
    # Lazy-import per-tool message helpers from the parent _nudge_messages module
    # to avoid circular import (the parent re-exports this function).
    from trw_mcp.state._nudge_messages import (
        _build_check_message,
        _checkpoint_message,
        _deliver_message,
        _learn_message,
        _review_message,
        _session_start_message,
    )

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


