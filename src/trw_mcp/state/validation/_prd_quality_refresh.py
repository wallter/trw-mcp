"""Dynamic repository-dependent PRD validation refresh."""

from __future__ import annotations

import time
from collections.abc import Callable

import structlog

from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.models.requirements import (
    DimensionScore,
    ImprovementSuggestion,
    ValidationFailure,
    ValidationResultV2,
)
from trw_mcp.state.validation.prd_integrity import build_path_index_partial_warning, run_prd_integrity_checks
from trw_mcp.state.validation.prd_quality import (
    _build_smell_suggestion,
    _check_sprint_deferral,
    classify_quality_tier,
    generate_improvement_suggestions,
    map_grade,
    score_implementation_readiness,
    score_traceability_v2,
)
from trw_mcp.state.validation.risk_profiles import get_risk_scaled_config

logger = structlog.get_logger(__name__)


# PRD-FIX-112: coarse dynamic check groups, in execution order. The wall-clock
# deadline is evaluated BETWEEN groups so this ordering is also the skip order:
# the grounded re-scoring runs first, the repo/wiring integrity groups last.
DYNAMIC_CHECK_GROUPS: tuple[str, ...] = (
    "dynamic_dimensions",
    "sprint_deferral",
    "integrity_checks",
    "wiring_gate",
)


def refresh_dynamic_prd_validation(
    base_result: ValidationResultV2,
    content: str,
    *,
    config: TRWConfig | None = None,
    project_root: str | None = None,
    deadline: float | None = None,
    fast: bool = False,
    budget_report: dict[str, object] | None = None,
    integrity_checker: Callable[..., tuple[list[ValidationFailure], list[str]]] = run_prd_integrity_checks,
    traceability_scorer: Callable[..., DimensionScore] = score_traceability_v2,
) -> ValidationResultV2:
    """Overlay current repository/date-dependent truth on a pure result.

    ``base_result`` may safely come from the persistent validation cache. This
    function deliberately re-runs every check whose answer can change while the
    PRD text is unchanged: grounded implementation/traceability dimensions,
    sprint deferrals, path/duplicate integrity, wiring reachability, seam
    expiry, and block-mode wiring failures.

    The persistent cache stores pure results. Repeated in-process refreshes may
    pass a prior dynamic result; serialized dynamic wire results are outputs,
    not refresh baselines, because their private pure-result provenance is not
    part of the public schema.

    PRD-FIX-112 (never-hang budget guard + fast mode):

    - *deadline* is an optional ``time.monotonic()`` timestamp. It is checked
      BETWEEN the coarse groups in :data:`DYNAMIC_CHECK_GROUPS` (and threaded
      into :func:`run_prd_integrity_checks` for finer sub-check granularity).
      On breach the remaining groups are SKIPPED — never raised, never silently
      passed. The historical 20-min hang was already fixed; this is the
      anti-regression guard so a future slowdown can never re-train gate bypass.
    - *fast* short-circuits every dynamic group using the SAME partial
      representation as a deadline breach (one representation for both causes).
    - *budget_report* (opt-in out-param) receives ``validation_partial: bool``
      and ``checks_skipped: list[str]`` so callers can surface them on the wire
      result without this pure model growing new fields. When any group is
      skipped a leading ``validation_partial:`` entry (naming the budget or fast
      mode) is prepended to ``integrity_warnings`` so a partial result is always
      *visibly* partial.
    """
    from pathlib import Path

    from trw_mcp.state.prd_utils import parse_frontmatter

    frontmatter = parse_frontmatter(content)
    root_path = Path(project_root) if project_root else None
    base_config = config or get_config()
    scaled_config = get_risk_scaled_config(base_config, base_result.effective_risk_level)
    baseline = base_result._dynamic_base_result or base_result
    static_baseline = baseline.model_copy(deep=True)
    static_baseline._dynamic_base_result = None
    result = static_baseline.model_copy(deep=True)
    result._dynamic_base_result = static_baseline

    checks_skipped: list[str] = []
    check_errors: list[str] = []

    def _budget_exhausted() -> bool:
        return fast or (deadline is not None and time.monotonic() > deadline)

    # --- Group 1: grounded dimension re-scoring ---------------------------
    if _budget_exhausted():
        checks_skipped.append("dynamic_dimensions")
        dimensions: list[DimensionScore] = list(result.dimensions)
    else:
        dynamic_scorers: dict[str, Callable[[], DimensionScore]] = {
            "implementation_readiness": lambda: score_implementation_readiness(
                frontmatter,
                content,
                scaled_config,
                project_root=root_path,
            ),
            "traceability": lambda: traceability_scorer(
                frontmatter,
                content,
                scaled_config,
                project_root=root_path,
            ),
        }
        dimensions = []
        for dimension in result.dimensions:
            scorer = dynamic_scorers.get(dimension.name)
            if scorer is None:
                dimensions.append(dimension)
                continue
            try:
                dimensions.append(scorer())
            except Exception:  # per-dimension fail-open behavior matches the base scorer
                logger.warning("dynamic_dimension_scoring_failed", dimension=dimension.name, exc_info=True)
                if "dynamic_dimensions" not in checks_skipped:
                    checks_skipped.append("dynamic_dimensions")
                    check_errors.append("dynamic_dimensions")
                dimensions.append(DimensionScore(name=dimension.name, score=0.0, max_score=dimension.max_score))
    result.dimensions = dimensions

    max_possible = sum(dimension.max_score for dimension in dimensions)
    result.total_score = (
        round(min(sum(dimension.score for dimension in dimensions) / max_possible * 100.0, 100.0), 2)
        if max_possible > 0
        else 0.0
    )
    result.quality_tier = classify_quality_tier(result.total_score, scaled_config)
    result.grade = map_grade(result.quality_tier)

    suggestions = generate_improvement_suggestions(dimensions)
    smell_suggestion = _build_smell_suggestion(result.smell_findings)
    if smell_suggestion is not None:
        suggestions.append(smell_suggestion)

    # --- Group 2: sprint-deferral status drift ----------------------------
    status_warnings = list(result.status_drift_warnings)
    if root_path is not None:
        if _budget_exhausted():
            checks_skipped.append("sprint_deferral")
        else:
            try:
                status_warnings.extend(_check_sprint_deferral(frontmatter, project_root=root_path))
            except Exception:  # justified: fail-open, advisory deferral check must not block scoring
                logger.warning("sprint_deferral_check_failed", exc_info=True)
                checks_skipped.append("sprint_deferral")
                check_errors.append("sprint_deferral")
    result.status_drift_warnings = status_warnings

    # --- Group 3: repo/path/duplicate integrity ---------------------------
    integrity_failures: list[ValidationFailure] = []
    integrity_warnings: list[str] = []
    if root_path is not None:
        if _budget_exhausted():
            checks_skipped.append("integrity_checks")
        else:
            try:
                extra_roots = [
                    path if (path := Path(raw)).is_absolute() else root_path / path
                    for raw in getattr(scaled_config, "additional_repo_roots", []) or []
                ]
                # Structured grounding-degrade signal: when the repo basename index
                # truncates at a runaway cap, bare-filename grounding is SKIPPED
                # (advisory-skip) and cannot detect hallucinated file references. A
                # silent skip is what previously let a broken (always-partial) index
                # masquerade as a passing check, so surface it LOUDLY in the result's
                # integrity_warnings (the tool serializes those into the MCP output).
                partial_report: dict[str, object] = {}
                integrity_skipped: list[str] = []
                integrity_failures, integrity_warnings = integrity_checker(
                    content,
                    frontmatter,
                    project_root=root_path,
                    prds_relative_path=scaled_config.prds_relative_path,
                    extra_roots=extra_roots,
                    partial_report=partial_report,
                    deadline=deadline,
                    skipped=integrity_skipped,
                )
                checks_skipped.extend(integrity_skipped)
                partial_warning = build_path_index_partial_warning(partial_report)
                if partial_warning is not None:
                    integrity_warnings = [partial_warning, *integrity_warnings]
            except Exception:  # justified: fail-open, validation must still return pure output
                logger.warning("prd_integrity_check_failed", exc_info=True)
                checks_skipped.append("integrity_checks")
                check_errors.append("integrity_checks")
    result.integrity_warnings = integrity_warnings

    # --- Group 4: wiring / seam gate --------------------------------------
    wiring_failures: list[ValidationFailure] = []
    if _budget_exhausted():
        checks_skipped.append("wiring_gate")
    else:
        try:
            from trw_mcp.state.validation._prd_scoring_wiring import check_wiring_gate

            wiring_mode = str(getattr(scaled_config, "wiring_gate_mode", "warn") or "warn")
            wiring_warnings, wiring_failures = check_wiring_gate(
                content,
                frontmatter,
                mode=wiring_mode,
                project_root=root_path,
            )
            suggestions.extend(
                ImprovementSuggestion(dimension="wiring", priority="medium", message=message)
                for message in wiring_warnings
            )
        except Exception:  # justified: fail-open, advisory gate must not block scoring
            logger.warning("wiring_gate_check_failed", exc_info=True)
            checks_skipped.append("wiring_gate")
            check_errors.append("wiring_gate")

    result.improvement_suggestions = suggestions
    result.failures = [*result.failures, *integrity_failures, *wiring_failures]
    result.valid = result.valid and not integrity_failures and not wiring_failures

    # PRD-FIX-112: a partial result is ALWAYS visibly partial (never a silent
    # pass). Prepend a loud marker naming the cause and expose the machine-
    # readable markers via the out-param.
    validation_partial = bool(checks_skipped)
    if validation_partial:
        budget = float(getattr(base_config, "prd_validate_budget_seconds", 60.0))
        joined = ", ".join(checks_skipped)
        if fast:
            marker = (
                "validation_partial: fast mode requested — the dynamic validation checks were "
                f"SKIPPED ({joined}). Repo/wiring/duplicate grounding was NOT performed; re-run "
                "without fast=True for a fully-grounded verdict."
            )
        elif check_errors:
            marker = (
                "validation_partial: dynamic validation check failure — check group(s) "
                f"FAILED ({', '.join(check_errors)}); all skipped groups: ({joined}). This result is PARTIAL; "
                "grounding for the skipped groups was NOT performed. Inspect warning logs and retry."
            )
        else:
            marker = (
                f"validation_partial: prd_validate_budget_seconds={budget:g}s budget exceeded — "
                f"remaining dynamic check group(s) SKIPPED ({joined}). This result is PARTIAL; "
                "grounding for the skipped groups was NOT performed."
            )
        result.integrity_warnings = [marker, *result.integrity_warnings]
    if budget_report is not None:
        budget_report["validation_partial"] = validation_partial
        budget_report["checks_skipped"] = list(checks_skipped)
    return result
