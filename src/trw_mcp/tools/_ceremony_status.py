"""Ceremony status helpers for live MCP tool responses."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.state._ceremony_progress_state import NudgeContext
from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.ceremony_progress import CeremonyState, read_ceremony_state
from trw_mcp.tools._ceremony_status_helpers import (
    _ContextualSelector as _ContextualSelector,
    _cached_bandit_weight as _cached_bandit_weight,
    _candidate_domains as _candidate_domains,
    _coerce_float as _coerce_float,
    _contextualize_candidates as _contextualize_candidates,
    _deterministic_fallback_text as _deterministic_fallback_text,
    _domain_match_score as _domain_match_score,
    _has_cached_learning_weights as _has_cached_learning_weights,
    _matches_inferred_domains as _matches_inferred_domains,
    _normalize_inferred_domains as _normalize_inferred_domains,
    _normalized_modified_files as _normalized_modified_files,
    _phase_match_score as _phase_match_score,
    _select_cached_or_deterministic_learning as _select_cached_or_deterministic_learning,
    _select_deterministic_fallback_learning as _select_deterministic_fallback_learning,
    _synthetic_nudge_learning_id as _synthetic_nudge_learning_id,
)
from trw_mcp.tools._ceremony_status_nudge import _try_learning_nudge_content as _try_learning_nudge_content

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig


def _load_config_for_trw_dir(trw_dir: Path) -> TRWConfig:
    """Load config.yaml from the active workspace instead of the global singleton."""

    import os

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.persistence import FileStateReader

    config_path = trw_dir / "config.yaml"
    if not config_path.exists():
        return TRWConfig.model_validate({"trw_dir": str(trw_dir)})

    try:
        overrides = FileStateReader().read_yaml(config_path)
        if not isinstance(overrides, dict):
            return TRWConfig.model_validate({"trw_dir": str(trw_dir)})
        filtered = {
            str(key): value
            for key, value in overrides.items()
            if value is not None and f"TRW_{str(key).upper()}" not in os.environ
        }
        filtered["trw_dir"] = str(trw_dir)
        return TRWConfig(**filtered)  # type: ignore[arg-type]
    except Exception:  # justified: fail-open, config read failure falls back to defaults
        logger.debug("workspace_config_load_failed", config_path=str(config_path), exc_info=True)
        return TRWConfig.model_validate({"trw_dir": str(trw_dir)})




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



def append_ceremony_status(
    response: dict[str, object],
    trw_dir: Path | None = None,
    context: NudgeContext | None = None,
) -> dict[str, object]:
    """Attach a live ceremony progress summary and nudge content to a tool response.

    Sets ``ceremony_status`` (always) and ``nudge_content`` (when a nudge pool
    is selected and produces content).

    Fail-open: if the state cannot be read, the original response is returned.
    """
    try:
        effective_dir = trw_dir if trw_dir is not None else resolve_trw_dir()
        cfg = _load_config_for_trw_dir(effective_dir)
        state = read_ceremony_state(effective_dir)
        response["ceremony_status"] = build_ceremony_status_line(state)

        if cfg.session_start_defer_under_writer_pressure:
            try:
                from trw_mcp.state.memory_pressure import should_defer_session_start_optional_work

                defer_nudge, writer_pids, defer_reason = should_defer_session_start_optional_work(
                    effective_dir,
                    threshold=cfg.session_start_writer_pressure_threshold,
                )
                if defer_nudge:
                    response["nudge_deferred"] = {
                        "reason": defer_reason,
                        "writer_pids": writer_pids,
                        "writer_count": len(writer_pids),
                        "threshold": cfg.session_start_writer_pressure_threshold,
                    }
                    logger.warning(
                        "ceremony_nudge_deferred",
                        reason=defer_reason,
                        writer_pids=writer_pids,
                        writer_count=len(writer_pids),
                        threshold=cfg.session_start_writer_pressure_threshold,
                    )
                    return response
            except Exception:  # justified: pressure detection is advisory and fail-open
                logger.debug("ceremony_nudge_pressure_check_failed", exc_info=True)

        from trw_mcp.state._ceremony_progress_state import (
            increment_nudge_count,
            increment_tool_call_counter,
            is_nudge_eligible,
            record_nudge_shown,
            record_pool_ignore,
            record_pool_nudge,
        )
        from trw_mcp.state.ceremony_nudge import (
            _compute_urgency,
            _context_reactive_message,
            _highest_priority_pending_step,
            _select_nudge_message,
            _select_nudge_pool,
            compute_nudge_minimal,
            select_contextual_nudge_content,
            select_learning_injection_content,
        )
        from trw_mcp.state.surface_tracking import log_surface_event

        # Increment tool call counter for cooldown tracking (PRD-CORE-134)
        try:
            increment_tool_call_counter(effective_dir)
            state.tool_call_counter += 1
        except Exception:  # justified: fail-open, cooldown tracking must not block ceremony status rendering
            logger.debug("ceremony_status_tool_counter_skipped", exc_info=True)

        if not cfg.effective_nudge_enabled:
            return response

        messenger = cfg.effective_nudge_messenger
        client_id = str(getattr(cfg.client_profile, "client_id", ""))

        def _pending_nudge_step() -> str:
            return _highest_priority_pending_step(state) or "session_start"

        def _record_emitted_nudge(
            *,
            messenger_name: str,
            pool_name: str,
            learning_id: str | None,
        ) -> str:
            step = _pending_nudge_step()
            try:
                increment_nudge_count(effective_dir, step)
            except Exception:  # justified: fail-open, count tracking must not block response decoration
                logger.debug("increment_nudge_count_failed", exc_info=True)

            effective_learning_id = learning_id or _synthetic_nudge_learning_id(
                messenger=messenger_name,
                pool=pool_name,
                step=step,
            )
            try:
                record_nudge_shown(effective_dir, effective_learning_id, state.phase, turn=state.tool_call_counter)
            except Exception:  # justified: fail-open
                logger.debug("record_nudge_shown_failed", exc_info=True)

            try:
                logger.info(
                    "nudge_shown",
                    pool=pool_name,
                    messenger=messenger_name,
                    learning_id=effective_learning_id,
                    phase=state.phase,
                    client_id=client_id,
                    turn=state.tool_call_counter,
                )
            except Exception:  # justified: fail-open per NFR02
                pass
            return effective_learning_id

        if messenger == "minimal":
            try:
                # The minimal messenger skips pool-based dispatch entirely —
                # it produces a compressed single-line nudge focused on the
                # highest-priority pending ceremony step. available_learnings
                # is cosmetic here (only used when pending == "session_start")
                # so pass 0 and let compute_nudge_minimal render its default.
                minimal_content = compute_nudge_minimal(state, available_learnings=0)
                if minimal_content:
                    response["nudge_content"] = minimal_content
                    logger.debug(
                        "nudge_messenger_selected",
                        messenger="minimal",
                        content_chars=len(minimal_content),
                    )
                    _record_emitted_nudge(
                        messenger_name="minimal",
                        pool_name="minimal",
                        learning_id=None,
                    )
            except Exception:  # justified: fail-open, never break ceremony status
                logger.debug("minimal_messenger_failed", exc_info=True)
            return response

        if messenger == "learning_injection":
            try:
                injected_content, learning_id, target_file = select_learning_injection_content(
                    state,
                    effective_dir,
                    skip_phase_duplicates=True,
                )
                if learning_id and not is_nudge_eligible(state, learning_id, state.phase):
                    try:
                        structlog.get_logger(__name__).debug(
                            "nudge_skipped",
                            reason="phase_dedup",
                            pool="learning_injection",
                            learning_id=learning_id,
                            client_id=str(getattr(cfg.client_profile, "client_id", "")),
                        )
                    except Exception:  # justified: fail-open per NFR02
                        pass
                    injected_content = None
                if injected_content:
                    response["nudge_content"] = injected_content
                    effective_learning_id = _record_emitted_nudge(
                        messenger_name="learning_injection",
                        pool_name="learning_injection",
                        learning_id=learning_id,
                    )
                    if learning_id:
                        try:
                            from trw_mcp.state._session_id import resolve_effective_session_id

                            log_surface_event(
                                effective_dir,
                                learning_id=learning_id,
                                surface_type="nudge",
                                phase=state.phase,
                                files_context=[target_file] if target_file else [],
                                exploration=False,
                                bandit_score=1.0,
                                client_profile=client_id,
                                model_family=cfg.model_family or "generic",
                                trw_version=cfg.framework_version,
                                session_id=resolve_effective_session_id(effective_dir),
                            )
                        except Exception:  # justified: fail-open
                            logger.debug("surface_event_log_failed", exc_info=True)
                    else:
                        _ = effective_learning_id
                    logger.debug(
                        "nudge_messenger_selected",
                        messenger="learning_injection",
                        content_chars=len(injected_content),
                    )
                else:
                    minimal_content = compute_nudge_minimal(state, available_learnings=0)
                    if minimal_content:
                        response["nudge_content"] = minimal_content
                        _record_emitted_nudge(
                            messenger_name="learning_injection",
                            pool_name="minimal",
                            learning_id=None,
                        )
            except Exception:  # justified: fail-open, never break ceremony status
                logger.debug("learning_injection_messenger_failed", exc_info=True)
            return response

        if messenger in {
            "contextual",
            "contextual_action",
            "contextual_distress",
            "silent_flow",
            "stepback",
            "anchor",
            "cod",
            "negative",
            "governance",
        }:
            try:
                contentual_content: str | None
                if messenger == "contextual_distress":
                    from trw_mcp.state.ceremony_nudge import compute_nudge_contextual_distress

                    contentual_content = compute_nudge_contextual_distress(state, effective_dir, context=context)
                    learning_id = None
                    target_file = None
                elif messenger == "silent_flow":
                    from trw_mcp.state.ceremony_nudge import compute_nudge_silent_flow

                    contentual_content = compute_nudge_silent_flow(state, effective_dir, context=context)
                    learning_id = None
                    target_file = None
                elif messenger == "stepback":
                    from trw_mcp.state.ceremony_nudge import compute_nudge_stepback

                    contentual_content = compute_nudge_stepback(state, effective_dir, context=context)
                    learning_id = None
                    target_file = None
                elif messenger == "anchor":
                    from trw_mcp.state.ceremony_nudge import compute_nudge_anchor

                    contentual_content = compute_nudge_anchor(state, effective_dir, context=context)
                    learning_id = None
                    target_file = None
                elif messenger == "cod":
                    from trw_mcp.state.ceremony_nudge import compute_nudge_cod

                    contentual_content = compute_nudge_cod(state)
                    learning_id = None
                    target_file = None
                elif messenger == "negative":
                    from trw_mcp.state.ceremony_nudge import compute_nudge_negative

                    contentual_content = compute_nudge_negative(state)
                    learning_id = None
                    target_file = None
                elif messenger == "governance":
                    from trw_mcp.state.ceremony_nudge import compute_nudge_governance

                    contentual_content = compute_nudge_governance(state)
                    learning_id = None
                    target_file = None
                else:
                    include_learning_caution = messenger == "contextual"
                    contentual_content, learning_id, target_file = select_contextual_nudge_content(
                        state,
                        effective_dir,
                        context=context,
                        skip_phase_duplicates=True,
                        include_learning_caution=include_learning_caution,
                    )

                if learning_id and not is_nudge_eligible(state, learning_id, state.phase):
                    try:
                        structlog.get_logger(__name__).debug(
                            "nudge_skipped",
                            reason="phase_dedup",
                            pool="contextual",
                            learning_id=learning_id,
                            client_id=str(getattr(cfg.client_profile, "client_id", "")),
                        )
                    except Exception:  # justified: fail-open per NFR02
                        pass
                    contentual_content = None

                if contentual_content:
                    response["nudge_content"] = contentual_content
                    _record_emitted_nudge(
                        messenger_name=messenger,
                        pool_name="context",
                        learning_id=learning_id,
                    )
                    if learning_id:
                        try:
                            from trw_mcp.state._session_id import resolve_effective_session_id

                            log_surface_event(
                                effective_dir,
                                learning_id=learning_id,
                                surface_type="nudge",
                                phase=state.phase,
                                files_context=[target_file] if target_file else [],
                                exploration=False,
                                bandit_score=1.0,
                                client_profile=client_id,
                                model_family=cfg.model_family or "generic",
                                trw_version=cfg.framework_version,
                                session_id=resolve_effective_session_id(effective_dir),
                            )
                        except Exception:  # justified: fail-open
                            logger.debug("surface_event_log_failed", exc_info=True)
                    logger.debug(
                        "nudge_messenger_selected",
                        messenger=messenger,
                        content_chars=len(contentual_content),
                        has_learning=bool(learning_id),
                    )
                else:
                    minimal_content = compute_nudge_minimal(state, available_learnings=0)
                    if minimal_content:
                        response["nudge_content"] = minimal_content
                        _record_emitted_nudge(
                            messenger_name=messenger,
                            pool_name="minimal",
                            learning_id=None,
                        )
            except Exception:  # justified: fail-open, never break ceremony status
                logger.debug("contextual_messenger_failed", exc_info=True)
            return response

        if messenger == "governance":
            try:
                from trw_mcp.state.ceremony_nudge import compute_nudge_governance

                gov_content = compute_nudge_governance(state)
                if gov_content:
                    response["nudge_content"] = gov_content
                    _record_emitted_nudge(
                        messenger_name="governance",
                        pool_name="governance",
                        learning_id=None,
                    )
            except Exception:  # justified: fail-open per NFR02
                logger.debug("governance_messenger_failed", exc_info=True)
            return response

        if messenger == "cod":
            try:
                from trw_mcp.state.ceremony_nudge import compute_nudge_cod

                cod_content = compute_nudge_cod(state)
                if cod_content:
                    response["nudge_content"] = cod_content
                    _record_emitted_nudge(
                        messenger_name="cod",
                        pool_name="cod",
                        learning_id=None,
                    )
            except Exception:  # justified: fail-open per NFR02
                logger.debug("cod_messenger_failed", exc_info=True)
            return response

        if messenger == "negative":
            try:
                from trw_mcp.state.ceremony_nudge import compute_nudge_negative

                neg_content = compute_nudge_negative(state)
                if neg_content:
                    response["nudge_content"] = neg_content
                    _record_emitted_nudge(
                        messenger_name="negative",
                        pool_name="negative",
                        learning_id=None,
                    )
            except Exception:  # justified: fail-open per NFR02
                logger.debug("negative_messenger_failed", exc_info=True)
            return response

        # 1. Select nudge pool via weighted random with cooldowns (standard messenger)
        weights = cfg.client_profile.nudge_pool_weights
        cooldown_after = cfg.nudge_pool_cooldown_after
        cooldown_calls = cfg.nudge_pool_cooldown_calls

        pool = _select_nudge_pool(state, weights, context, cooldown_after, cooldown_calls)
        if not pool:
            return response
        if pool != "learnings" and _has_cached_learning_weights(effective_dir):
            pool = "learnings"

        nudge_content: str | None = None

        # 2. Dispatch to pool-specific content generators
        if pool == "learnings":
            nudge_content = _try_learning_nudge_content(effective_dir, state)
        elif pool == "workflow":
            try:
                from trw_mcp.state._nudge_content import load_pool_message

                nudge_content = load_pool_message("workflow", phase_hint=state.phase)
            except ImportError:
                pass
        elif pool == "ceremony":
            pending = _highest_priority_pending_step(state)
            if pending:
                try:
                    from trw_mcp.state._nudge_content import load_pool_message

                    nudge_content = load_pool_message("ceremony", phase_hint=pending)
                except ImportError:
                    pass
                if not nudge_content:
                    # PRD-CORE-149 FR03: pass active profile so client-identity
                    # placeholders ({client_display_name}/{client_config_dir})
                    # substitute correctly for opencode/cursor/aider users.
                    nudge_content = _select_nudge_message(
                        pending, state, available_learnings=0, profile=cfg.client_profile
                    )
        elif pool == "context" and context:
            urgency = _compute_urgency(state, _highest_priority_pending_step(state) or "session_start")
            nudge_content = _context_reactive_message(context, state, urgency=urgency)

        # 3. Apply nudge content and update state
        if nudge_content:
            response["nudge_content"] = nudge_content
            if pool == "learnings":
                try:
                    increment_nudge_count(effective_dir, _pending_nudge_step())
                except Exception:  # justified: fail-open, count tracking must not block response decoration
                    logger.debug("increment_nudge_count_failed", exc_info=True)
            else:
                _record_emitted_nudge(
                    messenger_name="standard",
                    pool_name=pool,
                    learning_id=None,
                )
            record_pool_nudge(effective_dir, pool)
        else:
            # If a pool was selected but failed to produce content, record as ignore
            # so it enters cooldown and we try a different pool next time.
            record_pool_ignore(effective_dir, pool)

    except Exception:  # justified: status decoration must never break tool responses
        logger.debug("append_ceremony_status_failed", exc_info=True)
    return response
