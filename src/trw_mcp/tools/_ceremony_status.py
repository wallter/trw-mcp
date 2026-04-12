"""Ceremony status helpers for live MCP tool responses."""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.ceremony_progress import CeremonyState, read_ceremony_state

logger = structlog.get_logger(__name__)


def build_ceremony_status_line(state: CeremonyState) -> str:
    """Render a compact, deterministic summary of current ceremony progress."""
    parts = [
        "session_started" if state.session_started else "session_start_pending",
        f"phase={state.phase}",
        f"checkpoints={state.checkpoint_count}",
        f"learnings={state.learnings_this_session}",
    ]
    if state.build_check_result:
        parts.append(f"build={state.build_check_result}")
    if state.review_called:
        review_part = f"review={state.review_verdict or 'recorded'}"
        if state.review_p0_count:
            review_part = f"{review_part} p0={state.review_p0_count}"
        parts.append(review_part)
    if state.deliver_called:
        parts.append("deliver_called")
    return "; ".join(parts)


def _try_bandit_nudge_content(trw_dir: Path, state: CeremonyState) -> str | None:
    """Attempt to produce bandit-selected learning nudge content.

    Returns a nudge content string (may be multi-line on phase transition) or
    None when the bandit path is unavailable or produces no output.

    Addresses PRD-CORE-105 audit findings:
    - P0: calls bandit.update() with impact-based heuristic reward after selection
    - P0: saves state with C-5 envelope (client_profile, model_family, quarantined)
    - P1: applies nudge dedup from ceremony state before selection
    - P1: calls log_selection + log_surface_event with full metadata
    - P1: calls record_nudge_shown for dedup tracking
    - P1: wires phase_transition_withhold_rate from config
    - P1: routes through select_nudge_learning_bandit with decisions_out for metadata

    Always fail-open — never raises.
    """
    try:
        from trw_mcp.state._ceremony_progress_state import (
            is_nudge_eligible,
            record_nudge_shown,
        )
        from trw_mcp.state.bandit_policy import (
            WithholdingPolicy,
            _compute_heuristic_reward,
            load_bandit_state,
            render_nudge_content,
            resolve_client_class,
            save_bandit_state,
            select_nudge_learning_bandit,
        )
        from trw_mcp.state.memory_adapter import recall_learnings
        from trw_mcp.state.propensity_log import log_selection
        from trw_mcp.state.surface_tracking import log_surface_event

        # ── Resolve config metadata (best-effort, fail-open) ────────────────
        client_class = "full_mode"
        client_profile_name = ""
        model_family = ""
        phase_transition_withhold_rate = 0.10

        try:
            from trw_mcp.models.config import TRWConfig
            cfg = TRWConfig(trw_dir=str(trw_dir))
            client_profile_name = getattr(cfg.client_profile, "client_id", "") or ""
            client_class = resolve_client_class(client_profile_name)
            model_family = getattr(cfg, "model_family", "") or ""
            phase_transition_withhold_rate = float(
                getattr(cfg, "phase_transition_withhold_rate", 0.10)
            )
        except Exception:  # justified: config may not be available, use defaults
            pass

        # ── Recall candidates ────────────────────────────────────────────────
        candidates = recall_learnings(
            trw_dir,
            query="*",
            min_impact=0.5,
            max_results=10,
            compact=True,
        )
        if not candidates:
            return None

        # ── Dedup: filter candidates already shown in current phase (P1 fix) ─
        eligible_candidates = [
            c for c in candidates
            if is_nudge_eligible(state, str(c.get("id", "")), state.phase)
        ]
        if not eligible_candidates:
            # Fall back to full pool if all candidates are already deduplicated
            eligible_candidates = candidates

        # ── Load bandit state with C-5 envelope (P0 fix) ────────────────────
        bandit = load_bandit_state(trw_dir, client_class, model_family)
        policy = WithholdingPolicy(client_class=client_class)

        # ── Bandit selection with decisions captured for logging ─────────────
        decisions: list = []  # list[BanditDecision] populated by select_nudge_learning_bandit
        selected_learnings, is_transition = select_nudge_learning_bandit(
            eligible_candidates,
            bandit,
            policy,
            phase=state.phase,
            previous_phase=state.previous_phase,
            phase_transition_withhold_rate=phase_transition_withhold_rate,
            decisions_out=decisions,
        )

        if not selected_learnings:
            return None

        content = render_nudge_content(selected_learnings, is_transition)
        if not content:
            return None

        # ── P0: Update bandit posteriors with impact-based heuristic reward ──
        for learning in selected_learnings:
            arm_id = str(learning.get("id", ""))
            if arm_id:
                reward = _compute_heuristic_reward(learning)
                bandit.update(arm_id, reward)
                logger.debug(
                    "bandit_posterior_updated",
                    arm_id=arm_id,
                    reward=round(reward, 4),
                )

        # ── P0: Persist updated state with C-5 envelope (atomic) ─────────────
        try:
            save_bandit_state(trw_dir, bandit, client_class, model_family)
        except Exception:  # justified: state persistence must not block nudge
            logger.debug("bandit_state_persist_failed", exc_info=True)

        # Extract first decision for propensity metadata (P1 fix)
        first_decision = decisions[0] if decisions else None

        # ── P1: Record nudge in ceremony state for dedup ──────────────────────
        for learning in selected_learnings:
            arm_id = str(learning.get("id", ""))
            if arm_id:
                try:
                    record_nudge_shown(trw_dir, arm_id, state.phase)
                except Exception:  # justified: fail-open
                    logger.debug("record_nudge_shown_failed", exc_info=True)

        # ── P1: Surface event logging with metadata ───────────────────────────
        for learning in selected_learnings:
            arm_id = str(learning.get("id", ""))
            if arm_id:
                try:
                    log_surface_event(
                        trw_dir,
                        learning_id=arm_id,
                        surface_type="phase_transition" if is_transition else "nudge",
                        phase=state.phase,
                        exploration=(
                            first_decision.exploration if first_decision else False
                        ),
                        bandit_score=(
                            first_decision.selection_probability
                            if first_decision else 0.0
                        ),
                        client_profile=client_profile_name,
                        model_family=model_family,
                    )
                except Exception:  # justified: fail-open
                    logger.debug("surface_event_log_failed", exc_info=True)

        # ── P1: Propensity log with full BanditDecision metadata ──────────────
        primary = selected_learnings[0]
        primary_id = str(primary.get("id", ""))
        if primary_id:
            try:
                candidate_ids = [
                    str(c.get("id", "")) for c in candidates if c.get("id")
                ]
                runner_up = ""
                runner_up_prob = 0.0
                sel_prob = 1.0
                exploration = False
                if first_decision:
                    runner_up = first_decision.runner_up_id or ""
                    runner_up_prob = first_decision.runner_up_probability or 0.0
                    sel_prob = first_decision.selection_probability
                    exploration = first_decision.exploration

                log_selection(
                    trw_dir,
                    selected=primary_id,
                    candidate_set=candidate_ids,
                    runner_up=runner_up,
                    runner_up_probability=runner_up_prob,
                    selection_probability=sel_prob,
                    exploration=exploration,
                    context_phase=state.phase,
                    client_profile=client_profile_name,
                    model_family=model_family,
                )
            except Exception:  # justified: fail-open
                logger.debug("propensity_log_failed", exc_info=True)

        logger.info(
            "bandit_decision",
            selected=primary_id,
            phase=state.phase,
            is_transition=is_transition,
            client_class=client_class,
            sel_prob=round(
                first_decision.selection_probability if first_decision else 1.0, 4
            ),
            exploration=first_decision.exploration if first_decision else False,
        )

        return content

    except Exception:  # justified: nudge content must never block tool responses
        logger.debug("bandit_nudge_content_failed", exc_info=True)
        return None

        return None


def append_ceremony_status(
    response: dict[str, object],
    trw_dir: Path | None = None,
) -> dict[str, object]:
    """Attach a live ceremony progress summary and bandit nudge content to a tool response.

    Sets ``ceremony_status`` (always) and ``nudge_content`` (when bandit
    selection produces learning-backed content).

    Fail-open: if the state cannot be read, the original response is returned.
    """
    try:
        effective_dir = trw_dir if trw_dir is not None else resolve_trw_dir()
        state = read_ceremony_state(effective_dir)
        response["ceremony_status"] = build_ceremony_status_line(state)

        # Attempt bandit-backed learning nudge (PRD-CORE-105 FR04)
        nudge_content = _try_bandit_nudge_content(effective_dir, state)
        if nudge_content:
            response["nudge_content"] = nudge_content

    except Exception:  # justified: status decoration must never break tool responses
        logger.debug("append_ceremony_status_failed", exc_info=True)
    return response
