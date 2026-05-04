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


def format_nudge(template: str, profile: ClientProfile | None) -> str:
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
    profile: ClientProfile | None = None,
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



# Template selection extracted to _nudge_template_select (PRD-DIST-243 batch 23).
# Re-exported for back-compat with _ceremony_status.py + ceremony_nudge.py.
from trw_mcp.state._nudge_template_select import (
    _context_reactive_message as _context_reactive_message,
)
from trw_mcp.state._nudge_template_select import (
    _select_nudge_template as _select_nudge_template,
)



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



# Status-line builders extracted to _nudge_status_lines (PRD-DIST-243 batch 22).
# Re-exported for back-compat with ceremony_nudge.py imports.
from trw_mcp.state._nudge_status_lines import (
    _build_done_next_then_status as _build_done_next_then_status,
)
from trw_mcp.state._nudge_status_lines import (
    _build_done_next_then_status_light as _build_done_next_then_status_light,
)
from trw_mcp.state._nudge_status_lines import (
    _build_minimal_status_line as _build_minimal_status_line,
)
from trw_mcp.state._nudge_status_lines import (
    _build_status_line as _build_status_line,
)

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
