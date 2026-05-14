"""Specialized compute_nudge_* variants — extracted from ceremony_nudge.py for module-size compliance.

Belongs to the ``ceremony_nudge.py`` facade. Re-exported there for back-compat
with `_ceremony_status.py` which imports all 6 specialized variants via
the parent.

Six small composers that branch on specific state conditions:
- ``compute_nudge_silent_flow`` — pure silence unless distress detected
- ``compute_nudge_stepback`` — STOP/Step-back rescue nudge for distress
- ``compute_nudge_cod`` — Chain-of-Draft compact nudge
- ``compute_nudge_anchor`` — XML-tag-wrapped nudge for attention anchoring
- ``compute_nudge_negative`` — negative-constraint anti-pattern nudge
- ``compute_nudge_governance`` — minimal governance-only status line
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.state._nudge_state import CeremonyState, NudgeContext

logger = structlog.get_logger(__name__)


def compute_nudge_silent_flow(
    state: CeremonyState,
    trw_dir: Path,
    context: NudgeContext | None = None,
) -> str:
    """Render rich contextual nudge only if in distress; otherwise pure silence.

    Iter-25 'Zero-Tax' variant for high-performing models. Eliminates token overhead
    unless intervention is clinically necessary.
    """
    # Lazy-imports avoid the circular dep with parent ceremony_nudge.py.
    from trw_mcp.state.ceremony_nudge import _is_agent_in_distress, compute_nudge_contextual

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
    from trw_mcp.state._nudge_status_lines import _build_minimal_status_line
    from trw_mcp.state.ceremony_nudge import (
        _MINIMAL_HEADER,
        _is_agent_in_distress,
        compute_nudge_contextual,
        compute_nudge_minimal,
    )

    try:
        if _is_agent_in_distress(state):
            status = _build_minimal_status_line(state)
            msg = (
                f"{_MINIMAL_HEADER}\n{status}\n"
                "⚠ STOP. Step back. Identify the core principle or problem category. "
                "Form a hypothesis before your next tool call."
            )
            return msg
        return compute_nudge_contextual(state, trw_dir, context=context)
    except Exception:  # justified: fail-open per NFR02
        logger.debug("compute_nudge_stepback_failed", exc_info=True)
        return compute_nudge_minimal(state)


def compute_nudge_cod(state: CeremonyState) -> str:
    """Render a 'Chain of Draft' (CoD) shorthand nudge."""
    from trw_mcp.state._nudge_status_lines import _build_done_next_then_status_light
    from trw_mcp.state.ceremony_nudge import _MINIMAL_HEADER, compute_nudge_minimal

    try:
        status = _build_done_next_then_status_light(state)
        return f"{_MINIMAL_HEADER}\n{status}"
    except Exception:  # justified: fail-open per NFR02
        logger.debug("compute_nudge_cod_failed", exc_info=True)
        return compute_nudge_minimal(state)


def compute_nudge_anchor(state: CeremonyState, trw_dir: Path, context: NudgeContext | None = None) -> str:
    """Render a nudge wrapped in high-signal XML tags to anchor attention."""
    from trw_mcp.state.ceremony_nudge import compute_nudge_contextual, compute_nudge_minimal

    try:
        content = compute_nudge_contextual(state, trw_dir, context=context)
        return f"<TRW_SESSION_ANCHOR>\n{content}\n</TRW_SESSION_ANCHOR>"
    except Exception:  # justified: fail-open per NFR02
        logger.debug("compute_nudge_anchor_failed", exc_info=True)
        return compute_nudge_minimal(state)


def compute_nudge_negative(state: CeremonyState) -> str:
    """Render a nudge using negative constraints (anti-patterns)."""
    from trw_mcp.state._nudge_status_lines import _build_minimal_status_line
    from trw_mcp.state.ceremony_nudge import (
        _MINIMAL_HEADER,
        _highest_priority_pending_step,
        compute_nudge_minimal,
    )

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

        return f"{_MINIMAL_HEADER}\n{status}\n⚠ {msg}"
    except Exception:  # justified: fail-open per NFR02
        logger.debug("compute_nudge_negative_failed", exc_info=True)
        return compute_nudge_minimal(state)


def compute_nudge_governance(state: CeremonyState, available_learnings: int = 0) -> str:
    """Render a governance-only nudge (status line only, no prompt text)."""
    from trw_mcp.state._nudge_status_lines import _build_minimal_status_line
    from trw_mcp.state.ceremony_nudge import _MINIMAL_HEADER

    try:
        status_line = _build_minimal_status_line(state)
        return f"{_MINIMAL_HEADER}\n{status_line}"
    except Exception:  # justified: fail-open per NFR02
        logger.debug("compute_nudge_governance_failed", exc_info=True)
        return f"{_MINIMAL_HEADER}\n? start | ? deliver"
