"""trw_init helpers: complexity/task-type resolution and the REVIEW-mandate advisory.

Belongs to the ``orchestration.py`` facade. Kept in its own sibling so
``orchestration.py`` stays under the 350 effective-LOC gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
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
    objective: str = "",
    run_type: str,
    prd_scope: list[str] | None,
    task_type: str | None,
    complexity_hint: str | None,
    complexity_signals: dict[str, object] | None,
) -> InitProfile:
    """Resolve complexity + task-type + task_profile for ``trw_init``.

    Extracted from ``orchestration.py`` (PRD-CORE-060/134 + PRD-CORE-184) so
    the facade stays under the 350 eLOC gate.

    ``objective`` is forwarded to the detector (PRD-CORE-246-FR01) so the
    task-type classifier reads the free-text description. Before FR01 the
    detector saw only the
    regex-constrained ``task_name`` and the identifier-shaped ``prd_scope``.
    """
    from trw_mcp.models.run import ComplexityClass
    from trw_mcp.models.task_profile import resolve_task_profile
    from trw_mcp.tools._orchestration_helpers import _resolve_init_complexity
    from trw_mcp.tools._task_type_detection import detect_task_type

    parsed_signals, cclass, coverride, phase_reqs = _resolve_init_complexity(complexity_hint, complexity_signals)
    # PRD-CORE-184-FR02: heuristic task-type detection (no LLM call).
    detection = detect_task_type(
        task_name=task_name,
        objective=objective,
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
        tool_resolution_mode=config.tool_resolution_mode,
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
        # One clause, not three sentences: the only part the caller can act on
        # is "call trw_review before trw_deliver". That this run's complexity
        # overrides the session ceremony tier explains WHY the flag is set and
        # changes nothing the caller does, so it stays here in the source
        # rather than costing tokens in every init response.
        # The CONSEQUENCE is config-derived, never asserted. Under the shipped
        # default (review_gate_mode="warn") a missing review emits a soft
        # review_warning and delivery proceeds — so a flat "or delivery blocks"
        # is false for most callers, and this function's own docstring above
        # says it does not touch the deliver gate. Only "block" mode earns the
        # stronger clause.
        consequence = " Delivery blocks without one." if getattr(config, "review_gate_mode", "warn") == "block" else ""
        result["review_mandate_advisory"] = (
            f"REVIEW mandatory for this run — call trw_review before trw_deliver.{consequence}"
        )
    except Exception:  # justified: fail-open per NFR02 — advisory must not block init
        logger.debug("review_mandate_advisory_skipped", exc_info=True)


__all__ = [
    "InitProfile",
    "apply_review_mandate_advisory",
    "resolve_init_profile",
]
