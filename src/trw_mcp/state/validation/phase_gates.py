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

from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import ValidationFailure, ValidationResult
from trw_mcp.models.run import Phase
from trw_mcp.state.validation.phase_gates_build import (
    _BUILD_STALENESS_SECS as _BUILD_STALENESS_SECS,
    _best_effort_build_check as _best_effort_build_check,
    _best_effort_dry_check as _best_effort_dry_check,
    _best_effort_integration_check as _best_effort_integration_check,
    _best_effort_migration_check as _best_effort_migration_check,
    _best_effort_orphan_check as _best_effort_orphan_check,
    _best_effort_semantic_check as _best_effort_semantic_check,
    _check_build_status as _check_build_status,
)

# Re-export from sub-modules for backward compatibility
from trw_mcp.state.validation.phase_gates_prd import (
    _STATUS_ORDER as _STATUS_ORDER,
    _check_prd_enforcement as _check_prd_enforcement,
)

logger = structlog.get_logger(__name__)

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


from trw_mcp.state.validation._phase_gates_exits import (
    _EXIT_CHECKERS,
    _check_deliver_exit as _check_deliver_exit,
    _check_implement_exit as _check_implement_exit,
    _check_plan_exit as _check_plan_exit,
    _check_research_exit as _check_research_exit,
    _check_review_exit as _check_review_exit,
    _check_validate_exit as _check_validate_exit,
)


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


from trw_mcp.state.validation._phase_gates_inputs import (
    _INPUT_CHECKERS,
    _check_deliver_input as _check_deliver_input,
    _check_implement_input as _check_implement_input,
    _check_plan_input as _check_plan_input,
    _check_review_input as _check_review_input,
    _check_validate_input as _check_validate_input,
)


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
