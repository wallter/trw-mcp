"""Ceremony status helper cluster — extracted from _ceremony_status.py.

Belongs to the ``_ceremony_status.py`` facade. Re-exported there for
back-compat (test patches reach in via
``trw_mcp.tools._ceremony_status._has_cached_learning_weights``).

Contextual learning-nudge selection helpers: domain matching, phase
match scoring, deterministic fallback ordering, cached bandit-weight
ranking, and IntelligenceCache lookup.

Extracted as DIST-243 batch 48 to keep the parent ``_ceremony_status.py``
module under the 350 effective-LOC ceiling.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


def _synthetic_nudge_learning_id(
    *,
    messenger: str,
    pool: str,
    step: str,
) -> str:
    """Return a stable synthetic ID for non-learning nudge emissions."""
    normalized_messenger = messenger.replace("_", "-")
    normalized_pool = pool.replace("_", "-")
    return f"SYS-nudge-{normalized_messenger}-{normalized_pool}-{step}"


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
        domains.update(str(d).strip().lower() for d in raw_domains if str(d).strip())
    raw_tags = learning.get("tags")
    if isinstance(raw_tags, list):
        domains.update(str(t).strip().lower() for t in raw_tags if str(t).strip())
    return domains


def _matches_inferred_domains(learning: dict[str, object], inferred_domains: set[str]) -> bool:
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
        normalized_affinity = {str(v).strip().lower() for v in phase_affinity if str(v).strip()}
        if normalized_affinity:
            return 1.0 if normalized_phase in normalized_affinity else 0.1
    phase_origin = str(learning.get("phase_origin", "")).strip().lower()
    if phase_origin:
        return 0.8 if phase_origin == normalized_phase else 0.2
    return 0.5


def _domain_match_score(learning: dict[str, object], inferred_domains: set[str]) -> float:
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
    return [str(p).strip().lower() for p in modified_files if str(p).strip()]


def _normalize_inferred_domains(raw_domains: object) -> set[str]:
    """Return normalized inferred domains from best-effort recall context data."""
    if not isinstance(raw_domains, (list, tuple, set, frozenset)):
        return set()
    return {str(d).strip().lower() for d in raw_domains if str(d).strip()}


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
    inferred_domains = _normalize_inferred_domains(getattr(recall_context, "inferred_domains", set()))
    filtered_candidates = candidates
    if inferred_domains:
        domain_filtered = [c for c in candidates if _matches_inferred_domains(c, inferred_domains)]
        if domain_filtered:
            filtered_candidates = domain_filtered
    if len(filtered_candidates) < 2:
        return filtered_candidates
    if contextual_selector is None or not context_vector:
        return filtered_candidates
    shortlist_size = min(len(filtered_candidates), 5 if is_transition else 3)
    ranked_ids: list[str] = []
    remaining_ids = [str(c.get("id", "")) for c in filtered_candidates if c.get("id")]
    while remaining_ids and len(ranked_ids) < shortlist_size:
        selected_id, _ = contextual_selector.select(remaining_ids, context_vector=context_vector)
        ranked_ids.append(selected_id)
        remaining_ids = [arm_id for arm_id in remaining_ids if arm_id != selected_id]
    if not ranked_ids:
        return filtered_candidates
    candidate_map = {str(c.get("id", "")): c for c in filtered_candidates if c.get("id")}
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


def _cached_bandit_weight(learning: dict[str, object], bandit_params: dict[str, float] | None) -> float:
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
    contentful = [c for c in candidates if _deterministic_fallback_text(c)]
    if not contentful:
        return None
    if not bandit_params:
        return contentful[0]
    return max(
        contentful,
        key=lambda c: (
            _cached_bandit_weight(c, bandit_params),
            _phase_match_score(c, phase),
            _domain_match_score(c, inferred_domains),
            _coerce_float(c.get("impact", 0.0) or 0.0),
        ),
    )


def _has_cached_learning_weights(trw_dir: Path) -> bool:
    """Return True when backend-provided nudge weights are cached locally."""
    try:
        from trw_mcp.sync.cache import IntelligenceCache

        return bool(IntelligenceCache(trw_dir).get_bandit_params())
    except Exception:  # justified: cache lookup is advisory only
        return False
