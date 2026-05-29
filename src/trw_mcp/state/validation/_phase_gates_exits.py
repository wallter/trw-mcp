"""Phase exit checkers — extracted from phase_gates.py for module-size compliance.

Belongs to the ``phase_gates.py`` facade. Re-exported there for back-compat.

Six per-phase exit checkers + dispatch table. Each check signature is
``(run_path, config, failures) -> None`` — failures appended in-place.
Severity is encoded inside each checker (research/plan synthesis is
``warning``; deliver-time integration/orphan checks are ``error``).

Extracted as DIST-243 batch 33 to keep the parent ``phase_gates.py``
module under the 350 effective-LOC ceiling.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import PRDStatus, ValidationFailure
from trw_mcp.state.validation.event_helpers import (
    _REFLECTION_EVENTS,
    _SYNC_EVENTS,
    _events_contain,
    _read_events,
)
from trw_mcp.state.validation.phase_gates_build import (
    _best_effort_build_check,
    _best_effort_dry_check,
    _best_effort_integration_check,
    _best_effort_migration_check,
    _best_effort_orphan_check,
    _best_effort_semantic_check,
)
from trw_mcp.state.validation.phase_gates_prd import _check_prd_enforcement

logger = structlog.get_logger(__name__)


def _check_research_exit(
    run_path: Path,
    config: TRWConfig,
    failures: list[ValidationFailure],
) -> None:
    """Check RESEARCH phase exit criteria."""
    del config
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
    failures.extend(_check_prd_enforcement(run_path, config, PRDStatus.DRAFT, "plan"))


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
    try:
        required_status = PRDStatus(config.prd_required_status_for_implement)
    except ValueError:
        required_status = PRDStatus.APPROVED
    failures.extend(_check_prd_enforcement(run_path, config, required_status, "implement"))
    _best_effort_build_check(config, "implement", failures)


def _check_validate_exit(
    run_path: Path,
    config: TRWConfig,
    failures: list[ValidationFailure],
) -> None:
    """Check VALIDATE phase exit criteria."""
    del run_path
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
    del config
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
        from trw_mcp.state.analytics import compute_reflection_quality

        rq = compute_reflection_quality(_resolve_trw())
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
            if str(recon_data.get("verdict", "")) == "drift_detected":
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
                "DELIVER phase: run full test suite with coverage (use trw_build_check for automated verification)"
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
                message="Instruction file not synced — call trw_instructions_sync() before DELIVER",
                severity="warning",
            )
        )

    _best_effort_integration_check(failures, severity="error")
    _best_effort_orphan_check(failures, severity="error")
    _best_effort_build_check(config, "deliver", failures)


_PhaseChecker = Callable[[Path, TRWConfig, list[ValidationFailure]], None]

_EXIT_CHECKERS: dict[str, _PhaseChecker] = {
    "research": _check_research_exit,
    "plan": _check_plan_exit,
    "implement": _check_implement_exit,
    "validate": _check_validate_exit,
    "review": _check_review_exit,
    "deliver": _check_deliver_exit,
}
