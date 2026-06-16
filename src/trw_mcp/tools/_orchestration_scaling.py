"""trw_init cognitive-scaling wiring — PRD-SCALE-001 FR13/FR03.

Belongs to the ``orchestration.py`` facade. Kept in its own sibling so
``orchestration.py`` stays under the 350 effective-LOC gate (it was at the
ceiling when SCALE-001 wiring landed).

``run_scout_for_init`` is the verified consumer of the Scout on the real
``trw_init`` path: it invokes ``cognitive_scaling.classify`` (honoring a
``--planning-mode`` user override per FR13) and writes the session-layer
overlay ``meta/session_profile.yaml`` (FR03) that the H2 profile resolver
reads on the next ``trw_session_start``. Fail-open: any error degrades to
"no overlay written" and never blocks run init.

This module also owns the two init helpers extracted from
``orchestration.py`` to make room for the wiring (complexity resolution +
task-type detection), so the facade keeps a single import point.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from trw_mcp.models.cognitive_scaling import ScoutClassification
    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class InitProfile:
    """Resolved complexity + task-type + task_profile bundle for ``trw_init``."""

    parsed_signals: Any
    complexity_class: Any
    complexity_override: Any
    phase_requirements: Any
    detection: Any
    task_type: str
    task_profile: Any


def resolve_init_profile(
    config: TRWConfig,
    *,
    task_name: str,
    run_type: str,
    prd_scope: list[str] | None,
    task_type: str | None,
    complexity_hint: str | None,
    complexity_signals: dict[str, object] | None,
) -> InitProfile:
    """Resolve complexity + task-type + task_profile for ``trw_init``.

    Extracted from ``orchestration.py`` (PRD-CORE-060/134 + PRD-CORE-184) so
    the facade stays under the 350 eLOC gate after SCALE-001 FR13 wiring.
    """
    from trw_mcp.models.run import ComplexityClass
    from trw_mcp.models.task_profile import resolve_task_profile
    from trw_mcp.tools._orchestration_helpers import _resolve_init_complexity
    from trw_mcp.tools._task_type_detection import detect_task_type

    parsed_signals, cclass, coverride, phase_reqs = _resolve_init_complexity(complexity_hint, complexity_signals)
    # PRD-CORE-184-FR02: heuristic task-type detection (no LLM call).
    detection = detect_task_type(
        task_name=task_name,
        run_type=run_type,
        prd_scope=prd_scope,
        task_type=task_type,
    )
    task_profile = resolve_task_profile(
        client_profile=config.client_profile,
        model_tier=config.client_profile.default_model_tier,
        complexity_class=cclass or ComplexityClass.STANDARD,
        complexity_signals=parsed_signals,
        task_type=detection.task_type,
    )
    return InitProfile(
        parsed_signals=parsed_signals,
        complexity_class=cclass,
        complexity_override=coverride,
        phase_requirements=phase_reqs,
        detection=detection,
        task_type=detection.task_type,
        task_profile=task_profile,
    )


def apply_review_mandate_advisory(
    result: dict[str, str],
    *,
    phase_requirements: Any,
    config: TRWConfig,
) -> None:
    """Surface the up-front REVIEW-mandatory signal on a ``trw_init`` result.

    PRD-CORE-201 FR01/FR02. When the resolved run's ``phase_requirements`` list
    REVIEW as a mandatory phase (true for STANDARD/COMPREHENSIVE runs), this
    sets two result fields:

    - ``review_required = "true"`` (FR01) — a deterministic machine-readable
      signal that the run requires a REVIEW phase before deliver, regardless of
      the SessionStart ceremony tier the agent may have read up-front.
    - ``review_mandate_advisory`` (FR02) — a human-readable line that RECONCILES
      a possibly-misleading "Skip: REVIEW" session banner, stating the run
      complexity overrides the session ceremony tier.

    For runs where REVIEW is NOT mandatory (MINIMAL) or complexity is unresolved
    (``phase_requirements is None``), NO field is added — the absence of the
    field is the correct fail-open signal (NFR02), not ``"false"``.

    This is an ADVISORY ONLY. It does NOT change the CORE-192 deliver gate
    (NFR05). ``config.review_mandate_advisory_enabled`` (NFR04, default True)
    is the kill switch. Fully fail-open: any error leaves ``result`` unchanged.
    """
    try:
        if not config.review_mandate_advisory_enabled:
            return
        if phase_requirements is None:
            return
        mandatory = getattr(phase_requirements, "mandatory", None) or []
        if "REVIEW" not in [str(p).upper() for p in mandatory]:
            return
        result["review_required"] = "true"
        result["review_mandate_advisory"] = (
            "REVIEW: MANDATORY for this run. This run requires a REVIEW phase "
            "before deliver (run complexity overrides the session ceremony tier). "
            "Run trw_review before trw_deliver to avoid a deliver-time block."
        )
    except Exception:  # justified: fail-open per NFR02 — advisory must not block init
        logger.debug("review_mandate_advisory_skipped", exc_info=True)


#: Accepted ``--planning-mode`` override strings (FR13). Case-insensitive on
#: input; resolved to the closed ``PlanningMode`` enum.
_VALID_OVERRIDE_NAMES = ("DIRECT", "DUAL_DRAFT", "TRIANGULATED", "TRIANGULATED_WITH_PROBE")


def _resolve_override(planning_mode: str | None) -> object | None:
    """Resolve a ``--planning-mode`` string to a ``PlanningMode`` (FR13).

    Returns None when no override is supplied or the value is unrecognized
    (fail-open: an unknown override is ignored, not an error — Scout classifies
    normally).
    """
    if not planning_mode:
        return None
    from trw_mcp.models.cognitive_scaling import PlanningMode

    name = planning_mode.strip().upper()
    if name not in _VALID_OVERRIDE_NAMES:
        logger.warning(
            "scout_invalid_planning_mode_override",
            component="orchestration.scaling",
            value=planning_mode,
        )
        return None
    return PlanningMode[name]


def run_scout_for_init(
    config: TRWConfig,
    *,
    task_name: str,
    objective: str,
    prd_scope: list[str] | None,
    run_root: Path,
    project_root: Path,
    trw_dir: Path,
    planning_mode: str | None,
    result: dict[str, str],
) -> ScoutClassification | None:
    """Invoke the Scout on the real ``trw_init`` path and write the overlay.

    The verified SCALE-001 consumer: classifies the session, writes
    ``meta/session_profile.yaml`` (FR03) which the H2 resolver consumes,
    surfaces the planning mode + dynamic ceremony tier onto ``result``
    (FR01/FR03), and returns the classification. ``config.scout_enabled`` is
    the FR14 kill switch — when False, Scout never runs. Fully fail-open.
    """
    if not config.scout_enabled:
        return None
    try:
        from trw_mcp.cognitive_scaling import classify, write_session_profile

        override = _resolve_override(planning_mode)
        task_description = "\n".join(part for part in (task_name, objective, " ".join(prd_scope or [])) if part)
        classification = classify(
            task_description=task_description,
            declared_paths=list(prd_scope or []),
            project_root=project_root,
            trw_dir=trw_dir,
            blast_radius_threshold=config.scout_blast_radius_threshold,
            churn_commit_threshold=config.scout_churn_commit_threshold,
            override_mode=override,  # type: ignore[arg-type]
        )
        write_session_profile(classification, run_dir=run_root)
        result["planning_mode"] = classification.planning_mode.name
        result["scout_ceremony_tier"] = classification.ceremony_tier
        logger.info(
            "scout_init_classified",
            component="orchestration.scaling",
            op="run_scout_for_init",
            outcome="ok",
            planning_mode=classification.planning_mode.name,
            ceremony_tier=classification.ceremony_tier,
            source=classification.source,
        )
        return classification
    except Exception:  # justified: fail-open per FR12 — scaling must not block init
        logger.warning(
            "scout_init_failed",
            component="orchestration.scaling",
            op="run_scout_for_init",
            outcome="degraded",
            exc_info=True,
        )
        return None


__all__ = [
    "InitProfile",
    "apply_review_mandate_advisory",
    "resolve_init_profile",
    "run_scout_for_init",
]
