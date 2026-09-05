"""Heuristic task-type detection — PRD-CORE-184-FR02.

Belongs to the ``orchestration.py`` facade (imported there for ``trw_init``).

The detector infers a :data:`~trw_mcp.models.task_profile_types.TaskType` from
signals available at ``trw_init`` time. It is **heuristic-only** by design: a
model-driven classification step would re-introduce the iter-6
classification-as-priming harm (-24/-26pp on coding tasks, durable negative
finding). No provider, model, or inference symbol is imported here, and a test
(``test_detect_task_type_no_llm_calls``) guards that invariant.

Priority order — the order the CODE executes, first match wins
(PRD-CORE-246-FR02; the previous listing put ``run_type`` above the keyword scan
and did not match :func:`detect_task_type`):

1. Explicit caller override (``task_type="rca"``)
2. Keyword scan over the JOINED description text — ``task_name``, the free-text
   ``objective``, and ``prd_scope`` — case-insensitive, fixed keyword lists
3. ``run_type`` mapping (every :data:`TaskType` member maps to itself, plus the
   ``implementation`` legacy alias)
4. Fallback: ``unknown``

The joined text is byte-identical to the one the Scout classifier builds for the
same ``trw_init`` call (``_orchestration_scaling.run_scout_for_init``), so the
two classifiers in one call read the same input (PRD-CORE-246-FR01). This is
what makes ``objective`` participate at all: ``task_name`` is constrained to
``^[a-zA-Z0-9][a-zA-Z0-9_-]*$`` and ``prd_scope`` entries are identifiers, so
before FR01 no free-text description of the work reached the detector.

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

# ``run_type`` -> ``TaskType`` mapping (PRD-CORE-246-FR02). Every member of the
# ``TaskType`` vocabulary maps to itself, so a caller that already names the
# regime in ``run_type`` is not silently dropped to the fallback; ``implementation``
# is the legacy alias the framework's own ``run_type`` default still uses
# (``orchestration.trw_init``). The identity entries do NOT change evaluation
# order — the keyword scan still runs first, so a ``run_type`` cannot shadow a
# more specific description signal.
_RUN_TYPE_MAP: dict[str, TaskType] = {
    "implementation": "coding",
    "coding": "coding",
    "research": "research",
    "docs": "docs",
    "eval": "eval",
    "rca": "rca",
    "planning": "planning",
    "unknown": "unknown",
}

#: NFR03: the ONLY caller-supplied fragment echoed into a rationale is the
#: ``run_type`` repr. Truncated so a large caller string cannot inflate a
#: ``trw_init`` or ``trw_session_start`` response.
_MAX_RATIONALE_ECHO_CHARS: int = 64

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
            (``explicit_override`` | ``keyword`` | ``run_type`` | ``fallback``).
            ``prd_scope`` is no longer a distinct method: PRD-CORE-246-FR01
            folded the scope text into the single joined keyword scan.
        rationale: short human-readable explanation for the event log.
    """

    task_type: TaskType
    detection_method: str
    rationale: str


def _join_detection_text(task_name: str, objective: str, prd_scope: list[str] | None) -> str:
    """Build the classification text — byte-identical to the Scout's join.

    See ``_orchestration_scaling.run_scout_for_init``, which builds the same
    string for ``cognitive_scaling.classify``. Keeping ONE join means the two
    classifiers in a single ``trw_init`` can never read different inputs.
    """
    return "\n".join(part for part in (task_name, objective, " ".join(prd_scope or [])) if part)


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
    objective: str = "",
    run_type: str = "",
    prd_scope: list[str] | None = None,
    task_type: str | None = None,
) -> DetectionResult:
    """Infer a :data:`TaskType` from available ``trw_init`` signals.

    ``objective`` is the free-text description of the work and is scanned in the
    same joined text as ``task_name`` and ``prd_scope`` (PRD-CORE-246-FR01).
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

        # 2. Keyword scan over the joined description text (beats the run_type
        #    mapping so a "debug" task on an implementation run resolves to rca).
        keyword_hit = _scan_keywords(_join_detection_text(task_name, objective, prd_scope))
        if keyword_hit is not None:
            return DetectionResult(
                task_type=keyword_hit,
                detection_method="keyword",
                rationale=f"description keyword resolved task_type={keyword_hit}",
            )

        # 3. run_type mapping.
        mapped = _RUN_TYPE_MAP.get(run_type)
        if mapped is not None:
            echo = run_type[:_MAX_RATIONALE_ECHO_CHARS]
            return DetectionResult(
                task_type=mapped,
                detection_method="run_type",
                rationale=f"run_type={echo!r} mapped to task_type={mapped}",
            )

        # 4. Fallback. When a run_type WAS supplied but matched no entry, name
        #    it (bounded per NFR03) — "nothing matched" and "your run_type is not
        #    a task type" are different diagnoses and the caller can only act on
        #    the second one.
        unmapped = f"; run_type={run_type[:_MAX_RATIONALE_ECHO_CHARS]!r} matched no entry" if run_type else ""
        return DetectionResult(
            task_type="unknown",
            detection_method="fallback",
            rationale=f"no task-type signals matched; defaulted to unknown{unmapped}",
        )
    except Exception:  # justified: fail-open, detection must never block trw_init (NFR02)
        logger.warning("task_type_detection_failed", exc_info=True)
        return DetectionResult(
            task_type="unknown",
            detection_method="fallback",
            rationale="detection raised; defaulted to unknown",
        )
