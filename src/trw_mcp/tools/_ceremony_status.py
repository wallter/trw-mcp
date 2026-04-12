"""Ceremony status helpers for live MCP tool responses."""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.ceremony_progress import CeremonyState, read_ceremony_state

logger = structlog.get_logger(__name__)


def _candidate_domains(learning: dict[str, object]) -> set[str]:
    """Extract normalized domain labels from a learning entry."""
    domains: set[str] = set()
    raw_domains = learning.get("domain")
    if isinstance(raw_domains, list):
        domains.update(
            str(domain).strip().lower()
            for domain in raw_domains
            if str(domain).strip()
        )
    raw_tags = learning.get("tags")
    if isinstance(raw_tags, list):
        domains.update(
            str(tag).strip().lower()
            for tag in raw_tags
            if str(tag).strip()
        )
    return domains


def _matches_inferred_domains(
    learning: dict[str, object],
    inferred_domains: set[str],
) -> bool:
    """Return True when a learning overlaps the active inferred domains."""
    if not inferred_domains:
        return False
    return bool(_candidate_domains(learning) & inferred_domains)


def _phase_match_score(learning: dict[str, object], phase: str) -> float:
    """Estimate how relevant a learning is for the current phase."""
    normalized_phase = phase.strip().lower()
    if not normalized_phase:
        return 0.5

    phase_affinity = learning.get("phase_affinity")
    if isinstance(phase_affinity, list):
        normalized_affinity = {
            str(value).strip().lower()
            for value in phase_affinity
            if str(value).strip()
        }
        if normalized_affinity:
            return 1.0 if normalized_phase in normalized_affinity else 0.1

    phase_origin = str(learning.get("phase_origin", "")).strip().lower()
    if phase_origin:
        return 0.8 if phase_origin == normalized_phase else 0.2

    return 0.5


def _domain_match_score(
    learning: dict[str, object],
    inferred_domains: set[str],
) -> float:
    """Estimate overlap between the learning and the current inferred domains."""
    if not inferred_domains:
        return 0.5

    learning_domains = _candidate_domains(learning)
    if not learning_domains:
        return 0.2

    overlap = learning_domains & inferred_domains
    union = learning_domains | inferred_domains
    if not union:
        return 0.0
    return len(overlap) / len(union)


def _posterior_mean(bandit: object, arm_id: str) -> float:
    """Best-effort estimate of the Thompson posterior mean for an arm."""
    arms = getattr(bandit, "_arms", {})
    if not isinstance(arms, dict):
        return 0.5
    arm = arms.get(arm_id)
    if arm is None:
        return 0.5

    alpha = getattr(arm, "alpha", None)
    beta = getattr(arm, "beta", None)
    if alpha is None or beta is None:
        return 0.5

    total = float(alpha) + float(beta)
    if total <= 0:
        return 0.5
    return max(0.0, min(1.0, float(alpha) / total))


def _phase_progress(phase: str) -> float:
    """Map ceremony phase to a normalized session-progress scalar."""
    ordered_phases = ("research", "plan", "implement", "validate", "review", "deliver")
    try:
        return ordered_phases.index(phase.strip().lower()) / (len(ordered_phases) - 1)
    except ValueError:
        return 0.0


def _session_progress_label(phase: str) -> str:
    """Map ceremony phase to the propensity-log progress bucket."""
    normalized_phase = phase.strip().lower()
    if normalized_phase in {"research", "plan"}:
        return "early"
    if normalized_phase in {"review", "deliver"}:
        return "late"
    return "mid"


def _contextualize_candidates(
    candidates: list[dict[str, object]],
    *,
    bandit: object,
    recall_context: object | None,
    phase: str,
    is_transition: bool,
) -> list[dict[str, object]]:
    """Narrow the live candidate pool using the real recall context."""
    if not candidates or recall_context is None:
        return candidates

    inferred_domains = getattr(recall_context, "inferred_domains", set())
    if not isinstance(inferred_domains, set):
        inferred_domains = set()
    inferred_domains = {
        str(domain).strip().lower()
        for domain in inferred_domains
        if str(domain).strip()
    }

    filtered_candidates = candidates
    if inferred_domains:
        domain_filtered = [
            candidate
            for candidate in candidates
            if _matches_inferred_domains(candidate, inferred_domains)
        ]
        if domain_filtered:
            filtered_candidates = domain_filtered

    if len(filtered_candidates) < 2:
        return filtered_candidates

    try:
        from trw_memory.bandit import ContextualBanditSelector
        from trw_mcp.state.bandit_policy import ENGINEERING_CONTEXT_DIM, build_context_vector
    except Exception:  # justified: fail-open
        return filtered_candidates

    current_phase = str(getattr(recall_context, "current_phase", "") or phase).strip().lower()
    modified_files = getattr(recall_context, "modified_files", [])
    if not isinstance(modified_files, list):
        modified_files = []

    context_vector = build_context_vector(
        phase=current_phase,
        session_progress=_phase_progress(current_phase),
        domain_similarity=1.0 if inferred_domains else 0.0,
        files_count=len(modified_files),
    )

    selector = ContextualBanditSelector(feature_dim=ENGINEERING_CONTEXT_DIM)
    for candidate in filtered_candidates:
        arm_id = str(candidate.get("id", ""))
        if not arm_id:
            continue
        impact = candidate.get("impact", 0.5)
        try:
            impact_value = float(impact)
        except (TypeError, ValueError):
            impact_value = 0.5
        impact_value = max(0.0, min(1.0, impact_value))
        contextual_reward = max(
            0.0,
            min(
                1.0,
                (
                    (0.35 * _posterior_mean(bandit, arm_id))
                    + (0.35 * impact_value)
                    + (0.20 * _phase_match_score(candidate, current_phase))
                    + (0.10 * _domain_match_score(candidate, inferred_domains))
                ),
            ),
        )
        selector.update(arm_id, contextual_reward, context_vector=context_vector)

    shortlist_size = min(len(filtered_candidates), 5 if is_transition else 3)
    ranked_ids: list[str] = []
    remaining_ids = [
        str(candidate.get("id", ""))
        for candidate in filtered_candidates
        if candidate.get("id")
    ]
    while remaining_ids and len(ranked_ids) < shortlist_size:
        selected_id, _ = selector.select(remaining_ids, context_vector=context_vector)
        ranked_ids.append(selected_id)
        remaining_ids = [arm_id for arm_id in remaining_ids if arm_id != selected_id]

    if not ranked_ids:
        return filtered_candidates

    candidate_map = {
        str(candidate.get("id", "")): candidate
        for candidate in filtered_candidates
        if candidate.get("id")
    }
    return [candidate_map[arm_id] for arm_id in ranked_ids if arm_id in candidate_map]


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
            WithheldEvent,
            WithholdingPolicy,
            _compute_heuristic_reward,
            load_bandit_state_and_policy,
            render_nudge_content,
            resolve_client_class,
            save_bandit_state,
            select_nudge_learning_bandit,
        )
        from trw_mcp.state.memory_adapter import recall_learnings
        from trw_mcp.state.propensity_log import log_selection
        from trw_mcp.state.surface_tracking import log_surface_event
        from trw_mcp.tools._recall_impl import build_recall_context

        # ── Resolve config metadata (best-effort, fail-open) ────────────────
        client_class = "full_mode"
        client_profile_name = ""
        model_family = "generic"
        phase_transition_withhold_rate = 0.10

        try:
            from trw_mcp.models.config import TRWConfig
            cfg = TRWConfig(trw_dir=str(trw_dir))
            client_profile_name = getattr(cfg.client_profile, "client_id", "") or ""
            client_class = resolve_client_class(client_profile_name)
            # cfg.model_family is always non-empty via validator (P1-A fix)
            model_family = cfg.model_family or "generic"
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

        # ── Load bandit state and pre-populated policy with restored detectors ─
        bandit, policy = load_bandit_state_and_policy(trw_dir, client_class, model_family)
        recall_context = build_recall_context(trw_dir, "*")
        selection_candidates = _contextualize_candidates(
            eligible_candidates,
            bandit=bandit,
            recall_context=recall_context,
            phase=state.phase,
            is_transition=bool(state.previous_phase and state.previous_phase != state.phase),
        )
        if not selection_candidates:
            selection_candidates = eligible_candidates

        # ── Bandit selection with decisions captured for logging ─────────────
        decisions: list = []  # list[BanditDecision] populated by select_nudge_learning_bandit
        withheld_events: list[WithheldEvent] = []
        selected_learnings, is_transition = select_nudge_learning_bandit(
            selection_candidates,
            bandit,
            policy,
            phase=state.phase,
            previous_phase=state.previous_phase,
            phase_transition_withhold_rate=phase_transition_withhold_rate,
            decisions_out=decisions,
            withheld_events_out=withheld_events,
        )

        # ── P1-D: Log withheld phase-transition events to propensity.jsonl ───
        # Must happen before the early-return so withheld events are always
        # persisted even when no candidates were ultimately selected.
        candidate_ids_for_log = [
            str(c.get("id", "")) for c in selection_candidates if c.get("id")
        ]
        context_domains = (
            sorted(
                str(domain).strip()
                for domain in getattr(recall_context, "inferred_domains", set())
                if str(domain).strip()
            )
            if recall_context is not None else []
        )
        files_modified = (
            len(getattr(recall_context, "modified_files", []))
            if recall_context is not None
            and isinstance(getattr(recall_context, "modified_files", []), list)
            else 0
        )
        session_progress = _session_progress_label(state.phase)
        for wev in withheld_events:
            try:
                log_selection(
                    trw_dir,
                    selected=wev["learning_id"],
                    candidate_set=candidate_ids_for_log,
                    runner_up=wev["runner_up_id"],
                    selection_probability=wev["selection_probability"],
                    exploration=True,
                    withheld=True,
                    context_phase=state.phase,
                    context_domain=context_domains,
                    context_files_modified=files_modified,
                    context_session_progress=session_progress,
                    client_profile=client_profile_name,
                    model_family=model_family,
                )
            except Exception:  # justified: fail-open
                logger.debug("propensity_withheld_log_failed", exc_info=True)

        if not selected_learnings:
            return None

        content = render_nudge_content(selected_learnings, is_transition)
        if not content:
            return None

        # ── P0 + FR05: Update bandit posteriors and Page-Hinkley detectors ──
        for learning in selected_learnings:
            arm_id = str(learning.get("id", ""))
            if arm_id:
                reward = _compute_heuristic_reward(learning)
                bandit.update(arm_id, reward)
                # FR05: feed reward into per-arm Page-Hinkley detector so
                # trigger #4 (distributional shift) accumulates across calls
                alarm_fired = policy.update_reward(arm_id, reward)
                if alarm_fired:
                    bandit.soft_reset_arm(arm_id)
                logger.debug(
                    "bandit_posterior_updated",
                    arm_id=arm_id,
                    reward=round(reward, 4),
                    alarm_fired=alarm_fired,
                )

        # ── P0 + FR05: Persist updated bandit + detector states (atomic) ────
        try:
            save_bandit_state(trw_dir, bandit, client_class, model_family, policy=policy)
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
        for index, learning in enumerate(selected_learnings):
            arm_id = str(learning.get("id", ""))
            if arm_id:
                decision = decisions[index] if index < len(decisions) else None
                try:
                    log_surface_event(
                        trw_dir,
                        learning_id=arm_id,
                        surface_type="phase_transition" if is_transition else "nudge",
                        phase=state.phase,
                        exploration=decision.exploration if decision else False,
                        bandit_score=(
                            decision.selection_probability if decision else 0.0
                        ),
                        client_profile=client_profile_name,
                        model_family=model_family,
                    )
                except Exception:  # justified: fail-open
                    logger.debug("surface_event_log_failed", exc_info=True)

        # ── P1: Propensity log with full BanditDecision metadata ──────────────
        for index, learning in enumerate(selected_learnings):
            learning_id = str(learning.get("id", ""))
            if not learning_id:
                continue
            decision = decisions[index] if index < len(decisions) else None
            try:
                log_selection(
                    trw_dir,
                    selected=learning_id,
                    candidate_set=candidate_ids_for_log,
                    runner_up=decision.runner_up_id if decision and decision.runner_up_id else "",
                    runner_up_probability=(
                        decision.runner_up_probability
                        if decision and decision.runner_up_probability is not None
                        else 0.0
                    ),
                    selection_probability=decision.selection_probability if decision else 1.0,
                    exploration=decision.exploration if decision else False,
                    withheld=False,
                    context_phase=state.phase,
                    context_domain=context_domains,
                    context_files_modified=files_modified,
                    context_session_progress=session_progress,
                    client_profile=client_profile_name,
                    model_family=model_family,
                )
            except Exception:  # justified: fail-open
                logger.debug("propensity_log_failed", exc_info=True)

        primary = selected_learnings[0]
        primary_id = str(primary.get("id", ""))

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
