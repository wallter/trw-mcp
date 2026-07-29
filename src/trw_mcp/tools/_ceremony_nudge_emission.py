"""Nudge emission accounting — honest step attribution + universal telemetry.

Belongs to the ``_ceremony_status.py`` facade. Re-exported there for
back-compat.

Closes three framework-simplification ledger defects. Two of them made the nudge
telemetry describe something other than what the agent actually saw; the third
(UF-041, :func:`attach_reversion_prompt`) is a complete behaviour whose output
reached no response at all.

* **UF-023 — step attribution was a labelling artifact.** Every emission used
  to be counted against ``_highest_priority_pending_step(state) or
  "session_start"``, i.e. "whichever ceremony step is most overdue right now",
  not the step the nudge actually talked about. Once a project's ceremony state
  had every step satisfied (the steady state of any long-lived repo)
  ``_highest_priority_pending_step`` returns ``None`` and the ``or
  "session_start"`` default fired on *every* emission. Measured on this repo's
  live ``ceremony-state.json`` before the fix: 2,963 ``session_start`` vs 78
  ``build_check`` — against a state whose ``session_started`` had been ``True``
  for 4,777 tool calls. The distribution was noise.

  The rule here instead: attribute an emission to the ceremony step the emitted
  content is *about*, and to **nothing** when the content is a learning or a
  workflow message that names no step. ``CeremonyState.nudge_counts`` therefore
  stays keyed strictly by real ceremony step, which is what all three of its
  consumers already assume (``_compute_urgency`` progressive urgency,
  ``_reversion_prompt``'s scope-creep trigger, and
  ``nudge_analysis``'s responsiveness/resistance arithmetic — the last of which
  would mark any non-step key as permanently "resistant").

  Total *emission* volume is not lost: it moves to ``pool_nudge_counts``, which
  this module now writes on every pool rather than only the standard messenger's.

* **UF-024 — only 2 of 4 pools emitted a surface event.** The ``workflow`` and
  ``ceremony`` branches never called ``log_surface_event``, so live timing
  (``is_timely``) and A/B arm (``nudge_variant``) data covered a strict subset
  of ``total_nudges`` and could not be joined to it. Every emission routed
  through :func:`record_emitted_nudge` now logs one, using a synthetic
  ``SYS-nudge-*`` learning id when the pool is not learning-anchored.

* **UF-041 — the phase-reversion prompt reached nothing.** Its input was
  restored by commit ``48cf97b901``; :func:`attach_reversion_prompt` is the
  production caller that finally puts its output on a tool response.
"""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING

import structlog

from trw_mcp.tools._ceremony_status_helpers import (
    _emit_nudge_surface_event,
    _synthetic_nudge_learning_id,
)

if TYPE_CHECKING:
    from pathlib import Path

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state._ceremony_progress_state import CeremonyState, NudgeContext

logger = structlog.get_logger(__name__)

__all__ = [
    "account_nudge_emission",
    "attach_reversion_prompt",
    "record_emitted_nudge",
    "resolve_nudge_target_step",
]


def attach_reversion_prompt(
    response: dict[str, object],
    *,
    context: NudgeContext | None,
    state: CeremonyState,
) -> str | None:
    """Surface the phase-reversion prompt on ``response`` (ledger UF-041).

    ``_reversion_prompt`` had its INPUT restored by commit ``48cf97b901`` but
    its OUTPUT still reached nothing — zero production callers. This is that
    caller.

    Emitted as its own top-level ``reversion_prompt`` field rather than folded
    into ``nudge_content`` for two reasons: the three messenger branches of
    ``append_ceremony_status`` each return before pool dispatch, so a nudge-slot
    write would be reachable on only one path; and a build-failure / P0 /
    scope-creep signal must not be evictable by ``_assemble_nudge``'s character
    budget. Omitted entirely when no reversion applies, so it costs zero tokens
    on the overwhelmingly common path.

    Returns the prompt it wrote, or ``None``. Fail-open.
    """
    from trw_mcp.state.ceremony_nudge import _reversion_prompt

    try:
        prompt = _reversion_prompt(context, state)
    except Exception:  # justified: fail-open — advisory prompt must not break a tool response
        logger.debug("reversion_prompt_failed", exc_info=True)
        return None
    if not prompt:
        return None
    response["reversion_prompt"] = prompt
    with suppress(Exception):  # justified: fail-open per NFR02
        logger.info(
            "reversion_prompt_surfaced",
            phase=state.phase,
            tool_name=getattr(context, "tool_name", ""),
            build_passed=getattr(context, "build_passed", None),
            review_p0_count=getattr(context, "review_p0_count", 0),
        )
    return prompt


def _minimal_target_step(state: CeremonyState) -> str | None:
    """Mirror ``compute_nudge_minimal``'s own pending-step choice.

    The minimal messenger does NOT use ``_highest_priority_pending_step`` — it
    has its own two-branch ladder (``session_start`` -> ``deliver`` -> nothing).
    Attribution must follow the content, so it follows that ladder.
    """
    if not state.session_started:
        return "session_start"
    if not state.deliver_called:
        return "deliver"
    return None


def resolve_nudge_target_step(
    pool: str,
    state: CeremonyState,
    *,
    context: NudgeContext | None = None,
) -> str | None:
    """Return the ceremony step an emitted nudge actually addresses, else ``None``.

    ``None`` is a first-class answer, not a failure: a learning-injection or
    workflow nudge genuinely targets no ceremony step, and counting it against
    one is the UF-023 defect. Callers MUST NOT substitute a default.
    """
    if pool == "minimal":
        return _minimal_target_step(state)
    if pool == "ceremony":
        # resolve_pool_content("ceremony", ...) renders load_pool_message(
        # "ceremony", phase_hint=pending) — the pending step IS the target.
        from trw_mcp.state.ceremony_nudge import _highest_priority_pending_step

        return _highest_priority_pending_step(state)
    if pool == "context" and context is not None:
        # The reactive pool is force-selected by _select_nudge_pool on exactly
        # these two triggers, and _context_reactive_message renders the matching
        # build/review remediation text. Other reactive tools (init/recall/
        # status/prd_*) produce "what to do next" prose that names no ceremony
        # step, so they stay unattributed.
        if context.build_passed is False:
            return "build_check"
        if context.review_p0_count > 0:
            return "review"
    return None


def account_nudge_emission(
    trw_dir: Path,
    *,
    state: CeremonyState,
    pool: str,
    context: NudgeContext | None = None,
) -> str | None:
    """Record one emission in both ledgers; return the attributed step (or ``None``).

    ``nudge_counts`` (per ceremony step) is incremented only when the nudge
    actually targets a step. ``pool_nudge_counts`` (per pool) is incremented
    unconditionally, so total emission volume stays observable.

    Fail-open: counter failures must never break response decoration.
    """
    from trw_mcp.state._ceremony_progress_state import increment_nudge_count, record_pool_nudge

    target_step = resolve_nudge_target_step(pool, state, context=context)
    if target_step is not None:
        try:
            increment_nudge_count(trw_dir, target_step)
        except Exception:  # justified: fail-open, count tracking must not block decoration
            logger.debug("increment_nudge_count_failed", exc_info=True)
    try:
        record_pool_nudge(trw_dir, pool)
    except Exception:  # justified: fail-open
        logger.debug("record_pool_nudge_failed", exc_info=True)
    return target_step


def record_emitted_nudge(
    trw_dir: Path,
    *,
    state: CeremonyState,
    cfg: TRWConfig,
    messenger: str,
    pool: str,
    client_id: str,
    learning_id: str | None = None,
    target_file: str | None = None,
    context: NudgeContext | None = None,
) -> str:
    """Fully record one nudge emission and return its effective learning id.

    Accounting (:func:`account_nudge_emission`) + impression history +
    ``nudge_shown`` log + a surface event. The surface event fires for EVERY
    pool (UF-024); pools with no learning anchor get a stable synthetic
    ``SYS-nudge-*`` id so the stream can still be counted and joined.
    """
    from trw_mcp.state._ceremony_progress_state import record_nudge_shown

    target_step = account_nudge_emission(trw_dir, state=state, pool=pool, context=context)
    effective_learning_id = learning_id or _synthetic_nudge_learning_id(
        messenger=messenger,
        pool=pool,
        step=target_step or "unattributed",
    )
    try:
        record_nudge_shown(trw_dir, effective_learning_id, state.phase, turn=state.tool_call_counter)
    except Exception:  # justified: fail-open
        logger.debug("record_nudge_shown_failed", exc_info=True)

    with suppress(Exception):  # justified: fail-open per NFR02
        logger.info(
            "nudge_shown",
            pool=pool,
            messenger=messenger,
            learning_id=effective_learning_id,
            phase=state.phase,
            client_id=client_id,
            turn=state.tool_call_counter,
            nudge_step=target_step or "",
        )

    _emit_nudge_surface_event(
        trw_dir,
        cfg=cfg,
        state=state,
        messenger=messenger,
        client_id=client_id,
        learning_id=effective_learning_id,
        target_file=target_file,
        pending_step=target_step,
    )
    return effective_learning_id
