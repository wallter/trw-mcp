"""Ceremony status helpers for live MCP tool responses."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from typing import Protocol

import structlog

from trw_mcp.state._ceremony_progress_state import NudgeContext
from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.ceremony_progress import CeremonyState, read_ceremony_state

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


class _ContextualSelector(Protocol):
    """Protocol for optional contextual candidate selection."""

    def select(
        self,
        arm_ids: list[str],
        *,
        context_vector: list[float],
    ) -> tuple[str, float]: ...


def _candidate_domains(learning: dict[str, object]) -> set[str]:
    """Extract normalized domain labels from a learning entry."""
    domains: set[str] = set()
    raw_domains = learning.get("domain")
    if isinstance(raw_domains, list):
        domains.update(str(domain).strip().lower() for domain in raw_domains if str(domain).strip())
    raw_tags = learning.get("tags")
    if isinstance(raw_tags, list):
        domains.update(str(tag).strip().lower() for tag in raw_tags if str(tag).strip())
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
        normalized_affinity = {str(value).strip().lower() for value in phase_affinity if str(value).strip()}
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


def _normalized_modified_files(recall_context: object | None) -> list[str]:
    """Return best-effort normalized modified file paths from recall context."""
    modified_files = getattr(recall_context, "modified_files", [])
    if not isinstance(modified_files, list):
        return []
    return [str(path).strip().lower() for path in modified_files if str(path).strip()]


def _normalize_inferred_domains(raw_domains: object) -> set[str]:
    """Return normalized inferred domains from best-effort recall context data."""
    if not isinstance(raw_domains, (list, tuple, set, frozenset)):
        return set()
    return {str(domain).strip().lower() for domain in raw_domains if str(domain).strip()}


def _coerce_float(value: object, default: float = 0.0) -> float:
    """Best-effort float coercion for untyped learning payload values."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _contextualize_candidates(
    candidates: list[dict[str, object]],
    *,
    recall_context: object | None,
    is_transition: bool,
    contextual_selector: _ContextualSelector | None = None,
    context_vector: list[float] | None = None,
) -> list[dict[str, object]]:
    """Narrow the live candidate pool using the real recall context."""
    if not candidates or recall_context is None:
        return candidates

    inferred_domains: set[str] = _normalize_inferred_domains(
        getattr(recall_context, "inferred_domains", set()),
    )

    filtered_candidates = candidates
    if inferred_domains:
        domain_filtered = [
            candidate for candidate in candidates if _matches_inferred_domains(candidate, inferred_domains)
        ]
        if domain_filtered:
            filtered_candidates = domain_filtered

    if len(filtered_candidates) < 2:
        return filtered_candidates

    if contextual_selector is None or not context_vector:
        return filtered_candidates

    shortlist_size = min(len(filtered_candidates), 5 if is_transition else 3)
    ranked_ids: list[str] = []
    remaining_ids = [str(candidate.get("id", "")) for candidate in filtered_candidates if candidate.get("id")]
    while remaining_ids and len(ranked_ids) < shortlist_size:
        selected_id, _ = contextual_selector.select(
            remaining_ids,
            context_vector=context_vector,
        )
        ranked_ids.append(selected_id)
        remaining_ids = [arm_id for arm_id in remaining_ids if arm_id != selected_id]

    if not ranked_ids:
        return filtered_candidates

    candidate_map = {
        str(candidate.get("id", "")): candidate for candidate in filtered_candidates if candidate.get("id")
    }
    return [candidate_map[arm_id] for arm_id in ranked_ids if arm_id in candidate_map]


def _deterministic_fallback_text(learning: dict[str, object]) -> str:
    """Render the legacy deterministic learning text for backward compatibility."""
    nudge_line = learning.get("nudge_line")
    if isinstance(nudge_line, str) and nudge_line.strip():
        return nudge_line.strip()

    summary = learning.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()[:80]

    return ""


def _cached_bandit_weight(
    learning: dict[str, object],
    bandit_params: dict[str, float] | None,
) -> float:
    """Return the cached backend-provided bandit weight for one learning."""
    if not bandit_params:
        return 1.0
    learning_id = str(learning.get("id", ""))
    if not learning_id:
        return 1.0
    raw_score = bandit_params.get(learning_id)
    if raw_score is None:
        return 1.0
    try:
        return max(0.5, min(2.0, float(raw_score)))
    except (TypeError, ValueError):
        return 1.0


def _select_deterministic_fallback_learning(
    candidates: list[dict[str, object]],
) -> dict[str, object] | None:
    """Pick the first contentful learning from the deterministic ranking."""
    for candidate in candidates:
        if _deterministic_fallback_text(candidate):
            return candidate
    return None


def _select_cached_or_deterministic_learning(
    candidates: list[dict[str, object]],
    *,
    phase: str,
    inferred_domains: set[str],
    bandit_params: dict[str, float] | None,
) -> dict[str, object] | None:
    """Prefer cached backend weights, else preserve deterministic recall order."""
    contentful = [candidate for candidate in candidates if _deterministic_fallback_text(candidate)]
    if not contentful:
        return None
    if not bandit_params:
        return contentful[0]

    return max(
        contentful,
        key=lambda candidate: (
            _cached_bandit_weight(candidate, bandit_params),
            _phase_match_score(candidate, phase),
            _domain_match_score(candidate, inferred_domains),
            _coerce_float(candidate.get("impact", 0.0) or 0.0),
        ),
    )


def _has_cached_learning_weights(trw_dir: Path) -> bool:
    """Return True when backend-provided nudge weights are cached locally."""
    try:
        from trw_mcp.sync.cache import IntelligenceCache

        return bool(IntelligenceCache(trw_dir).get_bandit_params())
    except Exception:  # justified: cache lookup is advisory only
        return False


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


def _try_learning_nudge_content(trw_dir: Path, state: CeremonyState) -> str | None:
    """Attempt to produce cache-ranked or deterministic learning nudge content.

    Uses backend-provided cache weights when available, but never runs the
    backend-only local policy/state machine in the public client. Falls back to
    deterministic recall order when the cache is empty or stale.
    """
    try:
        from trw_mcp.state._ceremony_progress_state import (
            is_nudge_eligible,
            record_nudge_shown,
        )
        from trw_mcp.state.memory_adapter import recall_learnings
        from trw_mcp.state.surface_tracking import log_surface_event
        from trw_mcp.sync.cache import IntelligenceCache
        from trw_mcp.tools._recall_impl import build_recall_context

        # ── Resolve config metadata (best-effort, fail-open) ────────────────
        client_profile_name = ""
        model_family = "generic"

        try:
            from trw_mcp.models.config import TRWConfig

            cfg = TRWConfig.model_validate({"trw_dir": str(trw_dir)})
            client_profile_name = getattr(cfg.client_profile, "client_id", "") or ""
            model_family = cfg.model_family or "generic"
        except Exception:  # justified: config may not be available, use defaults
            logger.debug("ceremony_status_config_defaults", exc_info=True)

        # ── Recall candidates ────────────────────────────────────────────────
        candidates = recall_learnings(
            trw_dir,
            query="*",
            min_impact=0.5,
            max_results=10,
            compact=False,
        )
        if not candidates:
            return None

        # ── Dedup: filter candidates already shown in current phase (P1 fix) ─
        eligible_candidates = [c for c in candidates if is_nudge_eligible(state, str(c.get("id", "")), state.phase)]
        if not eligible_candidates:
            # Fall back to full pool if all candidates are already deduplicated
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

        inferred_domains: set[str] = _normalize_inferred_domains(
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

            try:
                logger.info(
                    "nudge_shown",
                    pool="learnings",
                    messenger="standard",
                    learning_id=learning_id,
                    phase=state.phase,
                    client_id=client_profile_name,
                    turn=state.tool_call_counter,
                )
            except Exception:  # justified: fail-open per NFR02
                pass

            try:
                from trw_mcp.state._session_id import resolve_effective_session_id

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

    except Exception:  # justified: nudge content must never block tool responses
        logger.debug("learning_nudge_content_failed", exc_info=True)
        return None


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
        from trw_mcp.state._ceremony_progress_state import (
            increment_tool_call_counter,
            is_nudge_eligible,
            record_pool_ignore,
            record_pool_nudge,
            record_nudge_shown,
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

        effective_dir = trw_dir if trw_dir is not None else resolve_trw_dir()
        state = read_ceremony_state(effective_dir)
        response["ceremony_status"] = build_ceremony_status_line(state)

        # Increment tool call counter for cooldown tracking (PRD-CORE-134)
        try:
            increment_tool_call_counter(effective_dir)
            state.tool_call_counter += 1
        except Exception:  # justified: fail-open, cooldown tracking must not block ceremony status rendering
            logger.debug("ceremony_status_tool_counter_skipped", exc_info=True)

        cfg = _load_config_for_trw_dir(effective_dir)
        if not cfg.effective_nudge_enabled:
            return response

        messenger = cfg.effective_nudge_messenger

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
                    injected_content = None
                if injected_content:
                    response["nudge_content"] = injected_content
                    if learning_id:
                        try:
                            record_nudge_shown(effective_dir, learning_id, state.phase, turn=state.tool_call_counter)
                        except Exception:  # justified: fail-open
                            logger.debug("record_nudge_shown_failed", exc_info=True)
                        try:
                            logger.info(
                                "nudge_shown",
                                pool="learning_injection",
                                messenger="learning_injection",
                                learning_id=learning_id,
                                phase=state.phase,
                                client_id=str(getattr(cfg.client_profile, "client_id", "")),
                                turn=state.tool_call_counter,
                            )
                        except Exception:  # justified: fail-open per NFR02
                            pass
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
                                client_profile=str(getattr(cfg.client_profile, "client_id", "")),
                                model_family=cfg.model_family or "generic",
                                trw_version=cfg.framework_version,
                                session_id=resolve_effective_session_id(effective_dir),
                            )
                        except Exception:  # justified: fail-open
                            logger.debug("surface_event_log_failed", exc_info=True)
                    logger.debug(
                        "nudge_messenger_selected",
                        messenger="learning_injection",
                        content_chars=len(injected_content),
                    )
                else:
                    minimal_content = compute_nudge_minimal(state, available_learnings=0)
                    if minimal_content:
                        response["nudge_content"] = minimal_content
            except Exception:  # justified: fail-open, never break ceremony status
                logger.debug("learning_injection_messenger_failed", exc_info=True)
            return response

        if messenger in {"contextual", "contextual_action"}:
            try:
                include_learning_caution = messenger == "contextual"
                contextual_content, learning_id, target_file = select_contextual_nudge_content(
                    state,
                    effective_dir,
                    context=context,
                    skip_phase_duplicates=True,
                    include_learning_caution=include_learning_caution,
                )
                if learning_id and not is_nudge_eligible(state, learning_id, state.phase):
                    contextual_content = None
                if contextual_content:
                    response["nudge_content"] = contextual_content
                    if learning_id:
                        try:
                            record_nudge_shown(effective_dir, learning_id, state.phase, turn=state.tool_call_counter)
                        except Exception:  # justified: fail-open
                            logger.debug("record_nudge_shown_failed", exc_info=True)
                        try:
                            logger.info(
                                "nudge_shown",
                                pool="context",
                                messenger=messenger,
                                learning_id=learning_id,
                                phase=state.phase,
                                client_id=str(getattr(cfg.client_profile, "client_id", "")),
                                turn=state.tool_call_counter,
                            )
                        except Exception:  # justified: fail-open per NFR02
                            pass
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
                                client_profile=str(getattr(cfg.client_profile, "client_id", "")),
                                model_family=cfg.model_family or "generic",
                                trw_version=cfg.framework_version,
                                session_id=resolve_effective_session_id(effective_dir),
                            )
                        except Exception:  # justified: fail-open
                            logger.debug("surface_event_log_failed", exc_info=True)
                    logger.debug(
                        "nudge_messenger_selected",
                        messenger=messenger,
                        content_chars=len(contextual_content),
                        has_learning=bool(learning_id),
                    )
                else:
                    minimal_content = compute_nudge_minimal(state, available_learnings=0)
                    if minimal_content:
                        response["nudge_content"] = minimal_content
            except Exception:  # justified: fail-open, never break ceremony status
                logger.debug("contextual_messenger_failed", exc_info=True)
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
                    nudge_content = _select_nudge_message(pending, state, available_learnings=0)
        elif pool == "context" and context:
            urgency = _compute_urgency(state, _highest_priority_pending_step(state) or "session_start")
            nudge_content = _context_reactive_message(context, state, urgency=urgency)

        # 3. Apply nudge content and update state
        if nudge_content:
            response["nudge_content"] = nudge_content
            record_pool_nudge(effective_dir, pool)
        else:
            # If a pool was selected but failed to produce content, record as ignore
            # so it enters cooldown and we try a different pool next time.
            record_pool_ignore(effective_dir, pool)

    except Exception:  # justified: status decoration must never break tool responses
        logger.debug("append_ceremony_status_failed", exc_info=True)
    return response
