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


def _normalized_modified_files(recall_context: object | None) -> list[str]:
    """Return best-effort normalized modified file paths from recall context."""
    modified_files = getattr(recall_context, "modified_files", [])
    if not isinstance(modified_files, list):
        return []
    return [
        str(path).strip().lower()
        for path in modified_files
        if str(path).strip()
    ]


def _contextualize_candidates(
    candidates: list[dict[str, object]],
    *,
    recall_context: object | None,
    is_transition: bool,
    contextual_selector: object | None = None,
    context_vector: list[float] | None = None,
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

    if contextual_selector is None or not context_vector:
        return filtered_candidates

    shortlist_size = min(len(filtered_candidates), 5 if is_transition else 3)
    ranked_ids: list[str] = []
    remaining_ids = [
        str(candidate.get("id", ""))
        for candidate in filtered_candidates
        if candidate.get("id")
    ]
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
        str(candidate.get("id", "")): candidate
        for candidate in filtered_candidates
        if candidate.get("id")
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
    contentful = [
        candidate
        for candidate in candidates
        if _deterministic_fallback_text(candidate)
    ]
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
            float(candidate.get("impact", 0.0) or 0.0),
        ),
    )


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
            cfg = TRWConfig(trw_dir=str(trw_dir))
            client_profile_name = getattr(cfg.client_profile, "client_id", "") or ""
            model_family = cfg.model_family or "generic"
        except Exception:  # justified: config may not be available, use defaults
            pass

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
        eligible_candidates = [
            c for c in candidates
            if is_nudge_eligible(state, str(c.get("id", "")), state.phase)
        ]
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

        inferred_domains = getattr(recall_context, "inferred_domains", set())
        if isinstance(inferred_domains, (list, tuple, set, frozenset)):
            normalized_domains = {
                str(domain).strip().lower()
                for domain in inferred_domains
                if str(domain).strip()
            }
        else:
            normalized_domains = set()
        bandit_params = IntelligenceCache(trw_dir).get_bandit_params()
        selected_learning = _select_cached_or_deterministic_learning(
            selection_candidates,
            phase=state.phase,
            inferred_domains=normalized_domains,
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
                record_nudge_shown(trw_dir, learning_id, state.phase)
            except Exception:  # justified: fail-open
                logger.debug("record_nudge_shown_failed", exc_info=True)

            try:
                log_surface_event(
                    trw_dir,
                    learning_id=learning_id,
                    surface_type="phase_transition" if is_transition else "nudge",
                    phase=state.phase,
                    exploration=False,
                    bandit_score=_cached_bandit_weight(selected_learning, bandit_params),
                    client_profile=client_profile_name,
                    model_family=model_family,
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
) -> dict[str, object]:
    """Attach a live ceremony progress summary and learning nudge content to a tool response.

    Sets ``ceremony_status`` (always) and ``nudge_content`` (when cache-ranked
    or deterministic learning selection produces content).

    Fail-open: if the state cannot be read, the original response is returned.
    """
    try:
        effective_dir = trw_dir if trw_dir is not None else resolve_trw_dir()
        state = read_ceremony_state(effective_dir)
        response["ceremony_status"] = build_ceremony_status_line(state)

        # Attempt cache-ranked / deterministic learning nudge.
        nudge_content = _try_learning_nudge_content(effective_dir, state)
        if nudge_content:
            response["nudge_content"] = nudge_content

    except Exception:  # justified: status decoration must never break tool responses
        logger.debug("append_ceremony_status_failed", exc_info=True)
    return response
