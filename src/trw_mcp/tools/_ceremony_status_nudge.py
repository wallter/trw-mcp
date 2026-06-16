"""Learning-nudge content selector — extracted from _ceremony_status.py.

Belongs to the ``_ceremony_status.py`` facade. Re-exported there for
back-compat.

Single helper:
- ``_try_learning_nudge_content`` — produces cache-ranked or deterministic
  learning nudge content for live MCP responses. Honors the dedup state
  machine, falls back to deterministic ranking when the IntelligenceCache
  is empty/stale, and emits structured surface_event telemetry.

Extracted as DIST-243 batch 51 to push parent ``_ceremony_status.py``
closer to the 350 effective-LOC ceiling.
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path

import structlog

from trw_mcp.state.ceremony_progress import CeremonyState
from trw_mcp.tools._ceremony_status_helpers import (
    _cached_bandit_weight,
    _contextualize_candidates,
    _deterministic_fallback_text,
    _normalize_inferred_domains,
    _select_cached_or_deterministic_learning,
)

logger = structlog.get_logger(__name__)


def _try_learning_nudge_content(trw_dir: Path, state: CeremonyState) -> str | None:
    """Attempt to produce cache-ranked or deterministic learning nudge content.

    Uses backend-provided cache weights when available, but never runs the
    backend-only local policy/state machine in the public client. Falls back to
    deterministic recall order when the cache is empty or stale.
    """
    try:
        from trw_mcp.state._ceremony_progress_state import is_nudge_eligible, record_nudge_shown
        from trw_mcp.state.recall_factories import recall_for_nudge_pool
        from trw_mcp.state.surface_tracking import log_surface_event
        from trw_mcp.sync.cache import IntelligenceCache
        from trw_mcp.tools._recall_impl import build_recall_context

        client_profile_name = ""
        model_family = "generic"
        nudge_variant_label = ""
        try:
            # PRD-FIX-085 FR03: use the per-process get_config() singleton
            from trw_mcp.models.config import get_config

            cfg = get_config()
            client_profile_name = getattr(cfg.client_profile, "client_id", "") or ""
            model_family = cfg.model_family or "generic"
            nudge_variant_label = cfg.nudge_variant or ""
        except Exception:  # justified: config may not be available, use defaults
            logger.debug("ceremony_status_config_defaults", exc_info=True)

        candidates = recall_for_nudge_pool(trw_dir, query="*", min_impact=0.5, max_results=10)
        if not candidates:
            return None

        # Dedup: filter candidates already shown in current phase (P1 fix)
        eligible_candidates = [c for c in candidates if is_nudge_eligible(state, str(c.get("id", "")), state.phase)]
        if not eligible_candidates:
            eligible_candidates = candidates

        recall_context = build_recall_context(trw_dir, "*")
        is_transition = bool(state.previous_phase and state.previous_phase != state.phase)
        selection_candidates = _contextualize_candidates(
            eligible_candidates,
            recall_context=recall_context,
            is_transition=is_transition,
        )
        if not selection_candidates:
            selection_candidates = eligible_candidates

        inferred_domains = _normalize_inferred_domains(
            getattr(recall_context, "inferred_domains", set()),
        )
        bandit_params = IntelligenceCache(trw_dir).get_bandit_params()
        selected_learning = _select_cached_or_deterministic_learning(
            selection_candidates,
            phase=state.phase,
            inferred_domains=inferred_domains,
            bandit_params=bandit_params,
        )
        if selected_learning is None:
            return None

        content = _deterministic_fallback_text(selected_learning)
        if not content:
            return None

        learning_id = str(selected_learning.get("id", ""))
        if learning_id:
            try:
                record_nudge_shown(trw_dir, learning_id, state.phase, turn=state.tool_call_counter)
            except Exception:  # justified: fail-open
                logger.debug("record_nudge_shown_failed", exc_info=True)

            with suppress(Exception):  # justified: fail-open per NFR02
                logger.info(
                    "nudge_shown",
                    pool="learnings",
                    messenger="standard",
                    learning_id=learning_id,
                    phase=state.phase,
                    client_id=client_profile_name,
                    turn=state.tool_call_counter,
                )

            try:
                from trw_mcp.state._session_id import resolve_effective_session_id
                from trw_mcp.state.ceremony_nudge import _highest_priority_pending_step
                from trw_mcp.state.nudge_analysis import compute_nudge_timing

                # Work targets #4/#6: stamp live timing + A/B arm/messenger only on
                # genuine nudges (not phase-transition surfaces). For transitions
                # the nudge-only fields stay empty/None so log_surface_event omits
                # them, keeping phase_transition events shape-compatible.
                nudge_step = ""
                is_timely: bool | None = None
                step_distance: int | None = None
                variant_label = ""
                messenger_label = ""
                if not is_transition:
                    nudge_step = _highest_priority_pending_step(state) or "session_start"
                    is_timely, step_distance = compute_nudge_timing(nudge_step, state)
                    variant_label = nudge_variant_label
                    messenger_label = "standard"
                log_surface_event(
                    trw_dir,
                    learning_id=learning_id,
                    surface_type="phase_transition" if is_transition else "nudge",
                    phase=state.phase,
                    exploration=False,
                    bandit_score=_cached_bandit_weight(selected_learning, bandit_params),
                    client_profile=client_profile_name,
                    model_family=model_family,
                    session_id=resolve_effective_session_id(trw_dir),
                    nudge_step=nudge_step,
                    is_timely=is_timely,
                    step_distance_from_call=step_distance,
                    nudge_variant=variant_label,
                    messenger=messenger_label,
                )
            except Exception:  # justified: fail-open
                logger.debug("surface_event_log_failed", exc_info=True)

        logger.info(
            "learning_nudge_selected",
            selected=learning_id,
            phase=state.phase,
            is_transition=is_transition,
            used_cached_bandit=bool(bandit_params),
        )
        return content
    except Exception:  # justified: fail-open, nudge generation must not block tool responses
        logger.debug("learning_nudge_content_failed", exc_info=True)
        return None
