"""Heuristic task-type detection — PRD-CORE-184-FR02.

Belongs to the ``orchestration.py`` facade (imported there for ``trw_init``).

The detector infers a :data:`~trw_mcp.models.task_profile_types.TaskType` from
signals available at ``trw_init`` time. It is **heuristic-only** by design: a
model-driven classification step would re-introduce the iter-6
classification-as-priming harm (-24/-26pp on coding tasks, durable negative
finding). No provider, model, or inference symbol is imported here, and a test
(``test_detect_task_type_no_llm_calls``) guards that invariant.

Priority order (first match wins):
1. Explicit caller override (``task_type="rca"``)
2. ``run_type`` mapping (``implementation`` -> coding, ``research`` -> research)
3. Task-description keyword scan (case-insensitive, fixed keyword lists)
4. ``prd_scope`` keyword scan (same lists, joined scope text)
5. Fallback: ``unknown``

The result is fail-open: any unexpected input degrades to ``unknown`` rather
than raising (NFR02 — detection must never block ``trw_init``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import get_args

import structlog

from trw_mcp.models.task_profile_types import TaskType

logger = structlog.get_logger(__name__)

_VALID_TASK_TYPES: frozenset[str] = frozenset(get_args(TaskType))

# ``run_type`` -> ``TaskType`` mapping. Only the two values the framework
# historically recognised are mapped; anything else falls through.
_RUN_TYPE_MAP: dict[str, TaskType] = {
    "implementation": "coding",
    "research": "research",
}

# Keyword lists scanned in priority order. RCA is checked before coding so
# "debug ... fix" resolves to rca (the more specific regime), and research is
# checked last so a generic "investigate" does not shadow a more specific
# coding/eval/planning signal earlier in a description.
_KEYWORD_ORDER: tuple[tuple[TaskType, tuple[str, ...]], ...] = (
    ("rca", ("debug", "rca", "root cause", "investigate", "trace", "stacktrace")),
    ("docs", ("document", "docs", "copywriting", "content", "readme", "write ")),
    ("eval", ("evaluate", "benchmark", "eval", "measure", "campaign", "score")),
    ("planning", ("sprint", "roadmap", "groom", "backlog", "plan")),
    ("coding", ("implement", "build", "fix", "refactor", "migrate", "feature", "add ")),
    ("research", ("research", "analyse", "analyze", "survey", "competitive")),
)


@dataclass(frozen=True)
class DetectionResult:
    """Outcome of :func:`detect_task_type`.

    Attributes:
        task_type: resolved behavioral regime.
        detection_method: which signal fired
            (``explicit_override`` | ``run_type`` | ``keyword`` | ``prd_scope``
            | ``fallback``).
        rationale: short human-readable explanation for the event log.
    """

    task_type: TaskType
    detection_method: str
    rationale: str


def _scan_keywords(text: str) -> TaskType | None:
    """Return the first task type whose keyword appears in ``text`` (lowered)."""
    if not text:
        return None
    lowered = text.lower()
    for task_type, keywords in _KEYWORD_ORDER:
        for kw in keywords:
            if kw in lowered:
                return task_type
    return None


def detect_task_type(
    *,
    task_name: str = "",
    run_type: str = "",
    prd_scope: list[str] | None = None,
    task_type: str | None = None,
) -> DetectionResult:
    """Infer a :data:`TaskType` from available ``trw_init`` signals.

    Heuristic-only; never raises (fail-open to ``unknown``).
    """
    try:
        # 1. Explicit override.
        if task_type and task_type in _VALID_TASK_TYPES:
            resolved: TaskType = task_type  # type: ignore[assignment]  # guarded by membership check
            return DetectionResult(
                task_type=resolved,
                detection_method="explicit_override",
                rationale=f"caller supplied explicit task_type={resolved}",
            )

        # 2. run_type mapping.
        mapped = _RUN_TYPE_MAP.get(run_type)

        # 3. Description keyword scan (beats the run_type mapping so a
        #    "debug" task on an implementation run resolves to rca).
        keyword_hit = _scan_keywords(task_name)
        if keyword_hit is not None:
            return DetectionResult(
                task_type=keyword_hit,
                detection_method="keyword",
                rationale=f"task_name keyword resolved task_type={keyword_hit}",
            )

        if mapped is not None:
            return DetectionResult(
                task_type=mapped,
                detection_method="run_type",
                rationale=f"run_type={run_type!r} mapped to task_type={mapped}",
            )

        # 4. prd_scope keyword scan.
        scope_text = " ".join(prd_scope) if prd_scope else ""
        scope_hit = _scan_keywords(scope_text)
        if scope_hit is not None:
            return DetectionResult(
                task_type=scope_hit,
                detection_method="prd_scope",
                rationale=f"prd_scope keyword resolved task_type={scope_hit}",
            )

        # 5. Fallback.
        return DetectionResult(
            task_type="unknown",
            detection_method="fallback",
            rationale="no task-type signals matched; conservative default applied",
        )
    except Exception:  # justified: fail-open, detection must never block trw_init (NFR02)
        logger.warning("task_type_detection_failed", exc_info=True)
        return DetectionResult(
            task_type="unknown",
            detection_method="fallback",
            rationale="detection raised; defaulted to unknown",
        )
