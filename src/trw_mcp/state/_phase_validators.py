"""Per-phase exit and input validators extracted from validation.py.

Each phase gets its own validator function that appends ValidationFailure
entries to a mutable list. A dispatch dict maps Phase -> validator for
clean O(1) lookup from the main check_phase_exit / check_phase_input
entry points.

This module is internal — import from trw_mcp.state.validation instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import PRDStatus, ValidationFailure
from trw_mcp.models.run import Phase


# Type alias for phase validator functions.
# Each takes (run_path, config, failures) and mutates the failures list.
PhaseExitValidator = Callable[[Path, TRWConfig, list[ValidationFailure]], None]
PhaseInputValidator = Callable[
    [Path, TRWConfig, list[ValidationFailure], str], None
]

# ---------------------------------------------------------------------------
# Shared helpers (imported lazily to avoid circular deps)
# ---------------------------------------------------------------------------

# Recognized event names — mirrors validation.py constants
_REFLECTION_EVENTS: frozenset[str] = frozenset(
    {"reflection_complete", "trw_reflect_complete"}
)
_SYNC_EVENTS: frozenset[str] = frozenset({"claude_md_sync", "claude_md_synced"})


def _read_events(events_path: Path) -> list[dict[str, object]]:
    """Read events.jsonl via FileStateReader (lazy import)."""
    if not events_path.exists():
        return []
    from trw_mcp.state.persistence import FileStateReader

    return FileStateReader().read_jsonl(events_path)


def _events_contain(
    events: list[dict[str, object]],
    event_names: frozenset[str],
) -> bool:
    """Check whether any event matches one of the given event names."""
    return any(e.get("event") in event_names for e in events)


def _is_validate_pass(event: dict[str, object]) -> bool:
    """Check if an event represents a passing validate phase gate.

    Delegates to validation._is_validate_pass (single source of truth).
    """
    from trw_mcp.state.validation import (
        _is_validate_pass as _impl,
    )

    return _impl(event)


def _best_effort_build_check(
    config: TRWConfig,
    phase_name: str,
    failures: list[ValidationFailure],
) -> None:
    """Append build-status failures (best-effort, never raises).

    Delegates to validation._best_effort_build_check.
    """
    from trw_mcp.state.validation import (
        _best_effort_build_check as _impl,
    )

    _impl(config, phase_name, failures)


def _best_effort_integration_check(
    failures: list[ValidationFailure],
    *,
    severity: str = "warning",
) -> None:
    """Append integration-check failures (best-effort, never raises).

    Delegates to validation._best_effort_integration_check.
    """
    from trw_mcp.state.validation import (
        _best_effort_integration_check as _impl,
    )

    _impl(failures, severity=severity)


def _check_prd_enforcement(
    run_path: Path,
    config: TRWConfig,
    required_status: PRDStatus,
    phase_name: str,
) -> list[ValidationFailure]:
    """Delegate to validation._check_prd_enforcement (lazy import)."""
    from trw_mcp.state.validation import (
        _check_prd_enforcement as _enforcement,
    )

    return _enforcement(run_path, config, required_status, phase_name)


# ===================================================================
# Phase EXIT validators
# ===================================================================

def validate_research_exit(
    run_path: Path,
    config: TRWConfig,
    failures: list[ValidationFailure],
) -> None:
    """Validate exit criteria for RESEARCH phase."""
    scratch_path = run_path / "scratch"
    reports_path = run_path / "reports"

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


def validate_plan_exit(
    run_path: Path,
    config: TRWConfig,
    failures: list[ValidationFailure],
) -> None:
    """Validate exit criteria for PLAN phase."""
    reports_path = run_path / "reports"

    plan_path = reports_path / "plan.md"
    if not plan_path.exists():
        failures.append(
            ValidationFailure(
                field="plan.md",
                rule="plan_exists",
                message="Plan document not found in reports/",
                severity="error",
            )
        )

    # PRD enforcement: verify PRDs exist and are at least DRAFT (FR04)
    prd_failures = _check_prd_enforcement(
        run_path, config, PRDStatus.DRAFT, "plan",
    )
    failures.extend(prd_failures)


def validate_implement_exit(
    run_path: Path,
    config: TRWConfig,
    failures: list[ValidationFailure],
) -> None:
    """Validate exit criteria for IMPLEMENT phase."""
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

    # PRD enforcement: verify PRDs meet required status for implement (FR05)
    required_status_str = config.prd_required_status_for_implement
    try:
        required_status = PRDStatus(required_status_str)
    except ValueError:
        required_status = PRDStatus.APPROVED
    prd_failures = _check_prd_enforcement(
        run_path, config, required_status, "implement",
    )
    failures.extend(prd_failures)

    # PRD-CORE-023-FR06: Build gate at IMPLEMENT (advisory only)
    _best_effort_build_check(config, "implement", failures)


def validate_validate_exit(
    run_path: Path,
    config: TRWConfig,
    failures: list[ValidationFailure],
) -> None:
    """Validate exit criteria for VALIDATE phase."""
    # Advisory: recommend integration + full-suite test strategy
    failures.append(
        ValidationFailure(
            field="test_strategy",
            rule="phase_test_advisory",
            message=(
                "VALIDATE phase: run integration tests and full suite "
                "(pytest tests/ -v -m 'not e2e' --cov)"
            ),
            severity="info",
        )
    )

    # PRD-QUAL-011-FR03: Integration check at VALIDATE
    _best_effort_integration_check(failures, severity="warning")

    # PRD-CORE-023-FR07: Build gate at VALIDATE
    _best_effort_build_check(config, "validate", failures)


def validate_review_exit(
    run_path: Path,
    config: TRWConfig,
    failures: list[ValidationFailure],
) -> None:
    """Validate exit criteria for REVIEW phase."""
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

    # Check for reflection event in events.jsonl
    events = _read_events(meta_path / "events.jsonl")
    if events:
        if not _events_contain(events, _REFLECTION_EVENTS):
            failures.append(
                ValidationFailure(
                    field="reflection",
                    rule="reflection_required",
                    message=(
                        "Reflection not completed — call trw_reflect() "
                        "before advancing past REVIEW"
                    ),
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
    except Exception:  # noqa: BLE001
        pass  # Best-effort advisory — never block on quality check failures


def validate_deliver_exit(
    run_path: Path,
    config: TRWConfig,
    failures: list[ValidationFailure],
) -> None:
    """Validate exit criteria for DELIVER phase."""
    meta_path = run_path / "meta"

    # Check run.yaml status
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
        except Exception:
            pass

    # Advisory: recommend full-suite + coverage at delivery
    failures.append(
        ValidationFailure(
            field="test_strategy",
            rule="phase_test_advisory",
            message=(
                "DELIVER phase: run full test suite with coverage "
                "(pytest tests/ -v --cov --cov-fail-under=85)"
            ),
            severity="info",
        )
    )

    # Check for CLAUDE.md sync event
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

    # PRD-QUAL-011-FR03: Integration check at DELIVER -- BLOCKING
    _best_effort_integration_check(failures, severity="error")

    # PRD-CORE-023-FR08: Build gate at DELIVER
    _best_effort_build_check(config, "deliver", failures)


# ===================================================================
# Phase EXIT dispatch table
# ===================================================================

PHASE_EXIT_DISPATCH: dict[str, PhaseExitValidator] = {
    "research": validate_research_exit,
    "plan": validate_plan_exit,
    "implement": validate_implement_exit,
    "validate": validate_validate_exit,
    "review": validate_review_exit,
    "deliver": validate_deliver_exit,
}


# ===================================================================
# Phase INPUT validators
# ===================================================================

def validate_research_input(
    run_path: Path,
    config: TRWConfig,
    failures: list[ValidationFailure],
    severity: str,
) -> None:
    """Validate input criteria for RESEARCH phase.

    Research has no per-phase prerequisites beyond run.yaml
    (which is checked universally before dispatch).
    """


def validate_plan_input(
    run_path: Path,
    config: TRWConfig,
    failures: list[ValidationFailure],
    severity: str,
) -> None:
    """Validate input criteria for PLAN phase."""
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


def validate_implement_input(
    run_path: Path,
    config: TRWConfig,
    failures: list[ValidationFailure],
    severity: str,
) -> None:
    """Validate input criteria for IMPLEMENT phase."""
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


def validate_validate_input(
    run_path: Path,
    config: TRWConfig,
    failures: list[ValidationFailure],
    severity: str,
) -> None:
    """Validate input criteria for VALIDATE phase."""
    shards_path = run_path / "shards"
    if not shards_path.exists() or not any(shards_path.iterdir()):
        failures.append(
            ValidationFailure(
                field="shards",
                rule="implementation_complete",
                message="No shard outputs found — complete implementation first",
                severity=severity,
            )
        )


def validate_review_input(
    run_path: Path,
    config: TRWConfig,
    failures: list[ValidationFailure],
    severity: str,
) -> None:
    """Validate input criteria for REVIEW phase."""
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


def validate_deliver_input(
    run_path: Path,
    config: TRWConfig,
    failures: list[ValidationFailure],
    severity: str,
) -> None:
    """Validate input criteria for DELIVER phase."""
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


# ===================================================================
# Phase INPUT dispatch table
# ===================================================================

PHASE_INPUT_DISPATCH: dict[str, PhaseInputValidator] = {
    "research": validate_research_input,
    "plan": validate_plan_input,
    "implement": validate_implement_input,
    "validate": validate_validate_input,
    "review": validate_review_input,
    "deliver": validate_deliver_input,
}
