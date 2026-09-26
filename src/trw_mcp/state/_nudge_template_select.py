"""Nudge template selection — extracted from _nudge_messages.py for module-size compliance.

Belongs to the ``_nudge_messages.py`` facade. Re-exported there for back-compat
with `ceremony_nudge.py` (and `_ceremony_status.py`) which import
`_context_reactive_message` via the parent.

PRD-QUAL-143 FR06 (R2-011): template bodies live in
``data/surfaces/nudge_template.yaml``, read via the existing
``load_pool_message`` loader (``_nudge_content.py``). This module keeps only
selection (which pool key applies) and ``{n}``/``{elapsed}`` substitution.

Two helpers:
- ``_select_nudge_template`` — selects the (step, case, urgency) template
- ``_context_reactive_message`` — context-reactive nudge text composer
"""

from __future__ import annotations

from datetime import datetime, timezone

from trw_mcp.state._nudge_content import load_pool_message
from trw_mcp.state._nudge_state import CeremonyState, NudgeContext, ToolName

# Tools whose reactive message renders its OWN failure branch and therefore
# still has something truthful to say when the call did not succeed:
# ``_build_check_message`` returns the "Build failed -> revert to PLAN" text,
# which is the single most valuable reactive nudge in the set. Every other
# branch below asserts the tool's action completed ("Progress saved.",
# "Session complete.", "Learning persisted.", "Run bootstrapped.") and MUST
# stay silent on a call that did not do it.
_FAILURE_AWARE_TOOLS: frozenset[str] = frozenset({ToolName.BUILD_CHECK})

# Static context-reactive tool messages with no dynamic content — direct
# pool-key lookups (PRD-QUAL-143 FR06).
_STATIC_CTX_KEYS: dict[str, str] = {
    ToolName.INIT: "ctx:init",
    ToolName.RECALL: "ctx:recall",
    ToolName.STATUS: "ctx:status",
    ToolName.PRD_VALIDATE: "ctx:prd_validate",
}


def _checkpoint_elapsed(state: CeremonyState) -> str:
    """Return ", N min ago" for the last checkpoint, or "" if unavailable."""
    if not state.last_checkpoint_ts:
        return ""
    try:
        last = datetime.fromisoformat(state.last_checkpoint_ts.replace("Z", "+00:00"))
        mins = int((datetime.now(timezone.utc) - last).total_seconds() / 60)
    except (ValueError, TypeError):  # trw-fail-silent-allow: fail-open -- elapsed display is cosmetic
        return ""
    return f", {mins} min ago" if mins > 0 else ""


def _select_nudge_template(step: str, state: CeremonyState, available_learnings: int) -> str:
    """Return the raw (pre-substitution) template for ``step`` at current urgency."""
    # Lazy-import to avoid circular dep: _nudge_messages.py imports from this module.
    from trw_mcp.state._nudge_messages import _compute_urgency

    urgency = _compute_urgency(state, step)

    if step == "session_start":
        case = "with" if available_learnings > 0 else "without"
        text = load_pool_message("template", f"session_start:{case}:{urgency}")
        return text.replace("{n}", str(available_learnings))

    if step == "checkpoint":
        n = state.files_modified_since_checkpoint
        elapsed = _checkpoint_elapsed(state)
        case = "with" if n > 0 else "without"
        text = load_pool_message("template", f"checkpoint:{case}:{urgency}")
        return text.replace("{n}", str(n)).replace("{elapsed}", elapsed)

    if step in ("build_check", "review"):
        return load_pool_message("template", f"{step}:{urgency}")

    if step == "deliver":
        return load_pool_message("template", "deliver")

    return ""


def _context_reactive_message(
    context: NudgeContext, state: CeremonyState, urgency: str = "low", ceremony_mode: str = "full"
) -> str | None:
    """Select context-reactive nudge message based on tool result.

    Returns None for an unknown ``tool_name`` (triggers fallback to static
    messages) and for a call that did not succeed, unless the tool is in
    :data:`_FAILURE_AWARE_TOOLS`.
    """
    # Lazy-import per-tool message helpers from the parent _nudge_messages module
    # to avoid circular import (the parent re-exports this function).
    from trw_mcp.state._nudge_messages import _build_check_message, _deliver_message, _review_message

    tool = context.tool_name

    # Truthfulness gate (CONSTITUTION §1). ``trw_checkpoint`` degrades softly:
    # with no resolvable run it returns ``recorded: False`` and writes nothing,
    # yet the same response is decorated by this layer. Keyed on the caller's
    # observed outcome, never on the response text, so a new soft-failure branch
    # is covered the moment its call site threads ``tool_success``.
    if not context.tool_success and tool not in _FAILURE_AWARE_TOOLS:
        return None

    if tool == ToolName.BUILD_CHECK:
        return _build_check_message(context, urgency)
    if tool == ToolName.REVIEW:
        return _review_message(context)
    if tool == ToolName.DELIVER:
        return _deliver_message(state)
    if tool == ToolName.CHECKPOINT:
        return load_pool_message("template", "ctx:checkpoint")
    if tool in (ToolName.LEARN, ToolName.SESSION_START):
        case = "light" if ceremony_mode == "light" else "full"
        return load_pool_message("template", f"ctx:{tool}:{case}")

    key = _STATIC_CTX_KEYS.get(tool)
    return load_pool_message("template", key) if key else None
