"""Standard-messenger nudge-pool selection + content resolution.

Belongs to the ``_ceremony_status.py`` facade. Re-exported there for
back-compat.

Two helpers covering the standard messenger's weighted-random pool
selection (with cooldowns + learning-cache override) and
pool-specific content generation. Both are read-only over the input
state — mutation (``increment_nudge_count``, ``record_pool_*``)
remains at the parent call site.

Extracted as DIST-243 batch 65 to push parent ``_ceremony_status.py``
under the 350-LOC gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.state._ceremony_progress_state import NudgeContext
from trw_mcp.tools._ceremony_status_helpers import (
    _has_cached_learning_weights,
)
from trw_mcp.tools._ceremony_status_nudge import _try_learning_nudge_content

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.ceremony_progress import CeremonyState

logger = structlog.get_logger(__name__)


def select_pool(
    state: CeremonyState,
    cfg: TRWConfig,
    context: NudgeContext | None,
    effective_dir: Path,
) -> str | None:
    """Pick the nudge pool for the standard messenger (cooldown + cache aware).

    Returns the resolved pool name, or ``None`` when no pool fires.
    Override: when ``_has_cached_learning_weights`` is set the pool is
    forced to ``"learnings"`` regardless of the weighted-random pick.
    """
    from trw_mcp.state.ceremony_nudge import _select_nudge_pool

    weights = cfg.client_profile.nudge_pool_weights
    cooldown_after = cfg.nudge_pool_cooldown_after
    cooldown_calls = cfg.nudge_pool_cooldown_calls

    pool = _select_nudge_pool(state, weights, context, cooldown_after, cooldown_calls)
    if not pool:
        return None
    if pool != "learnings" and _has_cached_learning_weights(effective_dir):
        return "learnings"
    return pool


def resolve_pool_content(
    pool: str,
    state: CeremonyState,
    cfg: TRWConfig,
    context: NudgeContext | None,
    effective_dir: Path,
) -> str | None:
    """Render pool-specific content for the standard messenger.

    Returns the rendered string, or ``None`` when the pool can't produce
    content (which causes the caller to record an ignore + cooldown).
    """
    from trw_mcp.state.ceremony_nudge import (
        _compute_urgency,
        _context_reactive_message,
        _highest_priority_pending_step,
        _select_nudge_message,
    )

    if pool == "learnings":
        return _try_learning_nudge_content(effective_dir, state)
    if pool == "workflow":
        try:
            from trw_mcp.state._nudge_content import load_pool_message

            return load_pool_message("workflow", phase_hint=state.phase)
        except ImportError:
            return None
    if pool == "ceremony":
        pending = _highest_priority_pending_step(state)
        if not pending:
            return None
        try:
            from trw_mcp.state._nudge_content import load_pool_message

            content = load_pool_message("ceremony", phase_hint=pending)
            if content:
                return content
        except ImportError:
            pass
        # PRD-CORE-149 FR03: pass active profile so client-identity
        # placeholders ({client_display_name}/{client_config_dir})
        # substitute correctly for opencode/cursor/aider users.
        return _select_nudge_message(pending, state, available_learnings=0, profile=cfg.client_profile)
    if pool == "context" and context:
        urgency = _compute_urgency(state, _highest_priority_pending_step(state) or "session_start")
        return _context_reactive_message(context, state, urgency=urgency)
    return None


def dispatch_contextual_messenger(
    messenger: str,
    state: CeremonyState,
    effective_dir: Path,
    context: NudgeContext | None,
) -> tuple[str | None, str | None, str | None]:
    """Resolve the contextual-cluster messenger to ``(content, learning_id, target_file)``.

    Handles 9 messenger names: ``contextual``, ``contextual_action``,
    ``contextual_distress``, ``silent_flow``, ``stepback``, ``anchor``,
    ``cod``, ``negative``, ``governance``. Six of these compute simple
    state-only content (learning_id/target_file always ``None``); the
    other three (``contextual``/``contextual_action``/``*default``) use
    ``select_contextual_nudge_content`` which can return a learning-anchored
    target.
    """
    from trw_mcp.state.ceremony_nudge import select_contextual_nudge_content

    if messenger == "contextual_distress":
        from trw_mcp.state.ceremony_nudge import compute_nudge_contextual_distress

        return compute_nudge_contextual_distress(state, effective_dir, context=context), None, None
    if messenger == "silent_flow":
        from trw_mcp.state.ceremony_nudge import compute_nudge_silent_flow

        return compute_nudge_silent_flow(state, effective_dir, context=context), None, None
    if messenger == "stepback":
        from trw_mcp.state.ceremony_nudge import compute_nudge_stepback

        return compute_nudge_stepback(state, effective_dir, context=context), None, None
    if messenger == "anchor":
        from trw_mcp.state.ceremony_nudge import compute_nudge_anchor

        return compute_nudge_anchor(state, effective_dir, context=context), None, None
    if messenger == "cod":
        from trw_mcp.state.ceremony_nudge import compute_nudge_cod

        return compute_nudge_cod(state), None, None
    if messenger == "negative":
        from trw_mcp.state.ceremony_nudge import compute_nudge_negative

        return compute_nudge_negative(state), None, None
    if messenger == "governance":
        from trw_mcp.state.ceremony_nudge import compute_nudge_governance

        return compute_nudge_governance(state), None, None
    # contextual / contextual_action: learning-anchored selection
    include_learning_caution = messenger == "contextual"
    return select_contextual_nudge_content(
        state,
        effective_dir,
        context=context,
        skip_phase_duplicates=True,
        include_learning_caution=include_learning_caution,
    )
