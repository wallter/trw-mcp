"""Phase gate validation -- exit and input criteria checks.

Implements check_phase_exit() and check_phase_input() which enforce
framework phase transitions by validating prerequisite artifacts,
PRD statuses, build results, and integration checks.

Sub-modules (extracted for focus):
- phase_gates_prd: PRD enforcement (_STATUS_ORDER, _check_prd_enforcement)
- phase_gates_build: Build checks (_BUILD_STALENESS_SECS, _check_build_status,
    _best_effort_build_check, _best_effort_integration_check,
    _best_effort_orphan_check)
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import PRDStatus, ValidationFailure, ValidationResult
from trw_mcp.models.run import Phase
from trw_mcp.state.validation.event_helpers import (
    _REFLECTION_EVENTS,
    _SYNC_EVENTS,
    _events_contain,
    _is_validate_pass,
    _read_events,
)
from trw_mcp.state.validation.phase_gates_build import (
    _BUILD_STALENESS_SECS as _BUILD_STALENESS_SECS,
)
from trw_mcp.state.validation.phase_gates_build import (
    _best_effort_build_check as _best_effort_build_check,
)
from trw_mcp.state.validation.phase_gates_build import (
    _best_effort_integration_check as _best_effort_integration_check,
)
from trw_mcp.state.validation.phase_gates_build import (
    _best_effort_orphan_check as _best_effort_orphan_check,
)
from trw_mcp.state.validation.phase_gates_build import (
    _check_build_status as _check_build_status,
)
from trw_mcp.state.validation.phase_gates_build import (
    _best_effort_dry_check as _best_effort_dry_check,
)
from trw_mcp.state.validation.phase_gates_build import (
    _best_effort_migration_check as _best_effort_migration_check,
)
from trw_mcp.state.validation.phase_gates_build import (
    _best_effort_semantic_check as _best_effort_semantic_check,
)

# Re-export from sub-modules for backward compatibility
from trw_mcp.state.validation.phase_gates_prd import (
    _STATUS_ORDER as _STATUS_ORDER,
)
from trw_mcp.state.validation.phase_gates_prd import (
    _check_prd_enforcement as _check_prd_enforcement,
)

logger = structlog.get_logger()

# Phase input criteria -- prerequisites to enter a phase (PRD-CORE-017-FR04).
PHASE_INPUT_CRITERIA: dict[str, list[str]] = {
    "research": [
        "Run initialized (run.yaml exists)",
    ],
    "plan": [
        "Research synthesis produced",
        "Run initialized (run.yaml exists)",
    ],
    "implement": [
        "Plan document exists (plan.md)",
        "Wave manifest defined (manifest.yaml)",
        "PRDs at required status",
    ],
    "validate": [
        "Implementation shards completed",
        "Output contracts available",
    ],
    "review": [
        "Validation phase passed",
        "Tests pass",
    ],
    "deliver": [
        "Review completed",
        "Reflection completed",
    ],
}

# Phase exit criteria descriptions (from FRAMEWORK.md sections)
PHASE_EXIT_CRITERIA: dict[str, list[str]] = {
    "research": [
        "All research shards complete or partial with findings",
        "Research synthesis produced",
        "Open questions documented",
    ],
    "plan": [
        "Plan document drafted (plan.md)",
        "Wave manifest defined",
        "Shard cards specified with output contracts",
    ],
    "implement": [
        "All implementation shards complete",
        "Output contracts validated",
        "No failed shards without recovery plan",
    ],
    "validate": [
        "Tests pass with required coverage",
        "Output contracts verified",
        "Risk register updated",
    ],
    "review": [
        "All findings reviewed",
        "Confidence levels assessed",
        "Final report drafted",
        "Reflection completed (reflection event in events.jsonl)",
    ],
    "deliver": [
        "Final report complete",
        "Artifacts organized",
        "Run state marked complete",
        "CLAUDE.md synced (claude_md_sync event in events.jsonl)",
    ],
}


def _build_phase_result(
    failures: list[ValidationFailure],
    criteria: list[str],
    phase_name: str,
    log_event: str,
) -> ValidationResult:
    """Build a ValidationResult from phase check failures.

    Shared by both exit and input phase checks to avoid duplication.

    Args:
        failures: Collected validation failures.
        criteria: Criteria list (used only for completeness denominator).
        phase_name: Phase name for logging.
        log_event: Event name for the log entry.

    Returns:
        ValidationResult with validity, failures, and completeness score.
    """
    is_valid = not any(f.severity == "error" for f in failures)
    result = ValidationResult(
        valid=is_valid,
        failures=failures,
        completeness_score=max(0.0, 1.0 - (len(failures) / max(len(criteria), 1))),
    )
    logger.info(log_event, phase=phase_name, valid=is_valid, failures=len(failures))
    return result


def _check_research_exit(
    run_path: Path,
    config: TRWConfig,
    failures: list[ValidationFailure],
) -> None:
    """Check RESEARCH phase exit criteria."""
    reports_path = run_path / "reports"
    scratch_path = run_path / "scratch"
    synthesis_path = scratch_path / "_orchestrator" / "research_synthesis.md"
    if not synthesis_path.exists():
        alt_path = reports_path / "research_synthesis.md"
        if not alt_path.exists():
            failures.append(
                ValidationFailure(
                    field="research_synthesis",
                    rule="synthesis_exists",
                    message="Research synthesis document not found",
                    severity="warning",
                )
            )


def _check_plan_exit(
    run_path: Path,
    config: TRWConfig,
    failures: list[ValidationFailure],
) -> None:
    """Check PLAN phase exit criteria."""
    plan_path = run_path / "reports" / "plan.md"
    if not plan_path.exists():
        failures.append(
            ValidationFailure(
                field="plan.md",
                rule="plan_exists",
                message="Plan document not found in reports/",
                severity="error",
            )
        )
    prd_failures = _check_prd_enforcement(
        run_path, config, PRDStatus.DRAFT, "plan",
    )
    failures.extend(prd_failures)


def _check_implement_exit(
    run_path: Path,
    config: TRWConfig,
    failures: list[ValidationFailure],
) -> None:
    """Check IMPLEMENT phase exit criteria."""
    shards_path = run_path / "shards"
    if shards_path.exists():
        manifest = shards_path / "manifest.yaml"
        if not manifest.exists():
            failures.append(
                ValidationFailure(
                    field="shards/manifest.yaml",
                    rule="manifest_exists",
                    message="Shard manifest not found",
                    severity="warning",
                )
            )
    required_status_str = config.prd_required_status_for_implement
    try:
        required_status = PRDStatus(required_status_str)
    except ValueError:
        required_status = PRDStatus.APPROVED
    prd_failures = _check_prd_enforcement(
        run_path, config, required_status, "implement",
    )
    failures.extend(prd_failures)
    _best_effort_build_check(config, "implement", failures)


def _check_validate_exit(
    run_path: Path,
    config: TRWConfig,
    failures: list[ValidationFailure],
) -> None:
    """Check VALIDATE phase exit criteria."""
    failures.append(
        ValidationFailure(
            field="test_strategy",
            rule="phase_test_advisory",
            message=(
                "VALIDATE phase: run integration tests and full suite "
                "with coverage (use trw_build_check for automated verification)"
            ),
            severity="info",
        )
    )
    _best_effort_integration_check(failures, severity="warning")
    _best_effort_orphan_check(failures, severity="warning")
    _best_effort_build_check(config, "validate", failures)
    _best_effort_dry_check(config, failures)
    _best_effort_migration_check(config, failures)
    _best_effort_semantic_check(config, failures)


def _check_review_exit(
    run_path: Path,
    config: TRWConfig,
    failures: list[ValidationFailure],
) -> None:
    """Check REVIEW phase exit criteria."""
    meta_path = run_path / "meta"
    reports_path = run_path / "reports"

    final_report = reports_path / "final.md"
    if not final_report.exists():
        failures.append(
            ValidationFailure(
                field="final.md",
                rule="final_report_exists",
                message="Final report not found in reports/",
                severity="warning",
            )
        )

    events = _read_events(meta_path / "events.jsonl")
    if events:
        if not _events_contain(events, _REFLECTION_EVENTS):
            failures.append(
                ValidationFailure(
                    field="reflection",
                    rule="reflection_required",
                    message="Reflection not completed — call trw_reflect() before advancing past REVIEW",
                    severity="warning",
                )
            )
    else:
        failures.append(
            ValidationFailure(
                field="reflection",
                rule="reflection_required",
                message="No events.jsonl found — reflection status unknown",
                severity="warning",
            )
        )

    # PRD-QUAL-012-FR05: Advisory reflection quality check
    try:
        from trw_mcp.state._paths import resolve_trw_dir as _resolve_trw

        trw_d = _resolve_trw()
        from trw_mcp.state.analytics import compute_reflection_quality

        rq = compute_reflection_quality(trw_d)
        rq_score = float(str(rq.get("score", 0.0)))
        if rq_score < 0.3:
            failures.append(
                ValidationFailure(
                    field="reflection_quality",
                    rule="reflection_quality_advisory",
                    message=(
                        f"Reflection quality score {rq_score:.2f} is below 0.30 — "
                        "consider improving reflection frequency and learning access"
                    ),
                    severity="warning",
                )
            )
    except Exception:  # justified: boundary, best-effort advisory check
        logger.debug("reflection_quality_check_failed", exc_info=True)

    # Spec reconciliation advisory (non-blocking)
    try:
        reconciliation_path = meta_path / "reconciliation.yaml"
        if reconciliation_path.exists():
            from trw_mcp.state.persistence import FileStateReader as _Reader

            recon_data = _Reader().read_yaml(reconciliation_path)
            recon_verdict = str(recon_data.get("verdict", ""))
            if recon_verdict == "drift_detected":
                mismatches = recon_data.get("mismatches", [])
                mismatch_count = len(mismatches) if isinstance(mismatches, list) else 0
                failures.append(
                    ValidationFailure(
                        field="spec_reconciliation",
                        rule="spec_drift_detected",
                        message=(
                            f"Spec reconciliation found {mismatch_count} identifier(s) "
                            "in PRD FRs not present in git diff — review for spec drift"
                        ),
                        severity="warning",
                    )
                )
        else:
            from trw_mcp.state.prd_utils import discover_governing_prds

            prds = discover_governing_prds(run_path)
            if prds:
                failures.append(
                    ValidationFailure(
                        field="spec_reconciliation",
                        rule="reconciliation_not_run",
                        message=(
                            "Spec reconciliation has not been run — "
                            "consider calling trw_review(mode='reconcile') "
                            f"for PRDs: {', '.join(prds)}"
                        ),
                        severity="info",
                    )
                )
    except Exception:  # justified: boundary, best-effort advisory check
        logger.debug("spec_reconciliation_check_failed", exc_info=True)


def _check_deliver_exit(
    run_path: Path,
    config: TRWConfig,
    failures: list[ValidationFailure],
) -> None:
    """Check DELIVER phase exit criteria."""
    meta_path = run_path / "meta"

    run_yaml = meta_path / "run.yaml"
    if run_yaml.exists():
        from trw_mcp.state.persistence import FileStateReader

        try:
            state = FileStateReader().read_yaml(run_yaml)
            if state.get("status") != "complete":
                failures.append(
                    ValidationFailure(
                        field="run.yaml:status",
                        rule="status_complete",
                        message="Run status not marked as complete",
                        severity="warning",
                    )
                )
        except Exception:  # justified: fail-open, run status check is advisory only
            logger.debug("run_status_check_failed", exc_info=True)

    failures.append(
        ValidationFailure(
            field="test_strategy",
            rule="phase_test_advisory",
            message=(
                "DELIVER phase: run full test suite with coverage "
                "(use trw_build_check for automated verification)"
            ),
            severity="info",
        )
    )

    events = _read_events(meta_path / "events.jsonl")
    if events and not _events_contain(events, _SYNC_EVENTS):
        failures.append(
            ValidationFailure(
                field="claude_md_sync",
                rule="sync_required",
                message="CLAUDE.md not synced — call trw_claude_md_sync() before DELIVER",
                severity="warning",
            )
        )

    _best_effort_integration_check(failures, severity="error")
    _best_effort_orphan_check(failures, severity="error")
    _best_effort_build_check(config, "deliver", failures)


# Type alias for phase validator functions
_PhaseChecker = Callable[[Path, TRWConfig, list[ValidationFailure]], None]

_EXIT_CHECKERS: dict[str, _PhaseChecker] = {
    "research": _check_research_exit,
    "plan": _check_plan_exit,
    "implement": _check_implement_exit,
    "validate": _check_validate_exit,
    "review": _check_review_exit,
    "deliver": _check_deliver_exit,
}


def check_phase_exit(
    phase: Phase,
    run_path: Path,
    config: TRWConfig,
) -> ValidationResult:
    """Check exit criteria for a framework phase.

    Dispatches to per-phase validator functions for focused,
    testable validation logic.

    Args:
        phase: Phase to validate exit criteria for.
        run_path: Path to the run directory.
        config: Framework configuration.

    Returns:
        ValidationResult with pass/fail and any failures.
    """
    failures: list[ValidationFailure] = []
    phase_name = phase.value
    criteria = PHASE_EXIT_CRITERIA.get(phase_name, [])

    checker = _EXIT_CHECKERS.get(phase_name)
    if checker is not None:
        checker(run_path, config, failures)

    return _build_phase_result(failures, criteria, phase_name, "phase_exit_checked")


def _check_plan_input(
    run_path: Path,
    config: TRWConfig,
    severity: str,
    failures: list[ValidationFailure],
) -> None:
    """Check PLAN phase input prerequisites."""
    scratch_path = run_path / "scratch"
    reports_path = run_path / "reports"
    synthesis_path = scratch_path / "_orchestrator" / "research_synthesis.md"
    alt_path = reports_path / "research_synthesis.md"
    if not synthesis_path.exists() and not alt_path.exists():
        failures.append(
            ValidationFailure(
                field="research_synthesis",
                rule="research_complete",
                message="Research synthesis not found — complete research phase first",
                severity=severity,
            )
        )


def _check_implement_input(
    run_path: Path,
    config: TRWConfig,
    severity: str,
    failures: list[ValidationFailure],
) -> None:
    """Check IMPLEMENT phase input prerequisites."""
    plan_path = run_path / "reports" / "plan.md"
    if not plan_path.exists():
        failures.append(
            ValidationFailure(
                field="plan.md",
                rule="plan_exists",
                message="Plan document not found — complete plan phase first",
                severity=severity,
            )
        )
    manifest_path = run_path / "shards" / "manifest.yaml"
    if not manifest_path.exists():
        failures.append(
            ValidationFailure(
                field="manifest.yaml",
                rule="manifest_exists",
                message="Wave manifest not found — define shard cards in plan phase",
                severity=severity,
            )
        )
    prd_failures = _check_prd_enforcement(
        run_path, config, PRDStatus.APPROVED, "implement",
    )
    failures.extend(prd_failures)


def _check_validate_input(
    run_path: Path,
    config: TRWConfig,
    severity: str,
    failures: list[ValidationFailure],
) -> None:
    """Check VALIDATE phase input prerequisites."""
    shards_path = run_path / "shards"
    try:
        shards_empty = not shards_path.exists() or not any(shards_path.iterdir())
    except OSError:
        shards_empty = True
    if shards_empty:
        failures.append(
            ValidationFailure(
                field="shards",
                rule="implementation_complete",
                message="No shard outputs found — complete implementation first",
                severity=severity,
            )
        )


def _check_review_input(
    run_path: Path,
    config: TRWConfig,
    severity: str,
    failures: list[ValidationFailure],
) -> None:
    """Check REVIEW phase input prerequisites."""
    meta_path = run_path / "meta"
    events = _read_events(meta_path / "events.jsonl")
    if events and not any(_is_validate_pass(e) for e in events):
        failures.append(
            ValidationFailure(
                field="validate_phase",
                rule="validate_passed",
                message="Validate phase gate not passed — complete validation first",
                severity=severity,
            )
        )


def _check_deliver_input(
    run_path: Path,
    config: TRWConfig,
    severity: str,
    failures: list[ValidationFailure],
) -> None:
    """Check DELIVER phase input prerequisites."""
    meta_path = run_path / "meta"
    events = _read_events(meta_path / "events.jsonl")
    if events:
        if not _events_contain(events, _REFLECTION_EVENTS):
            failures.append(
                ValidationFailure(
                    field="reflection",
                    rule="reflection_complete",
                    message="Reflection not completed — call trw_reflect before delivery",
                    severity=severity,
                )
            )
    else:
        failures.append(
            ValidationFailure(
                field="events.jsonl",
                rule="events_exist",
                message="No events log found — run appears incomplete",
                severity=severity,
            )
        )


# Input checker type: (run_path, config, severity, failures) -> None
_InputChecker = Callable[[Path, TRWConfig, str, list[ValidationFailure]], None]

_INPUT_CHECKERS: dict[str, _InputChecker] = {
    "plan": _check_plan_input,
    "implement": _check_implement_input,
    "validate": _check_validate_input,
    "review": _check_review_input,
    "deliver": _check_deliver_input,
    # research: no per-phase prerequisites beyond run.yaml
}


def check_phase_input(
    phase: Phase,
    run_path: Path,
    config: TRWConfig,
) -> ValidationResult:
    """Check input criteria (prerequisites) for entering a framework phase.

    Dispatches to per-phase input checker functions for focused,
    testable validation logic. When config.strict_input_criteria
    is True, missing prerequisites are errors; otherwise they are warnings.

    Args:
        phase: Phase to validate entry into.
        run_path: Path to the run directory.
        config: Framework configuration.

    Returns:
        ValidationResult with pass/fail and any failures.
    """
    failures: list[ValidationFailure] = []
    phase_name = phase.value
    criteria = PHASE_INPUT_CRITERIA.get(phase_name, [])
    severity = "error" if config.strict_input_criteria else "warning"

    meta_path = run_path / "meta"

    # Universal: run.yaml must exist -- early return since nothing else can be checked
    run_yaml = meta_path / "run.yaml"
    if not run_yaml.exists():
        failures.append(
            ValidationFailure(
                field="run.yaml",
                rule="run_initialized",
                message="Run not initialized — run.yaml missing",
                severity="error",
            )
        )
        return ValidationResult(
            valid=False,
            failures=failures,
            completeness_score=0.0,
        )

    # Dispatch to per-phase input checker
    checker = _INPUT_CHECKERS.get(phase_name)
    if checker is not None:
        checker(run_path, config, severity, failures)

    return _build_phase_result(failures, criteria, phase_name, "phase_input_checked")
