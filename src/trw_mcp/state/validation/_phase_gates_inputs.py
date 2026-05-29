"""Phase input checkers — extracted from phase_gates.py for module-size compliance.

Belongs to the ``phase_gates.py`` facade. Re-exported there for back-compat.

Five per-phase input checkers + dispatch table. Each check signature is
``(run_path, config, severity, failures) -> None`` — failures appended
in-place by the checker.

Extracted as DIST-243 batch 33 to keep the parent ``phase_gates.py``
module under the 350 effective-LOC ceiling.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import PRDStatus, ValidationFailure
from trw_mcp.state.validation.event_helpers import (
    _REFLECTION_EVENTS,
    _events_contain,
    _is_validate_pass,
    _read_events,
)
from trw_mcp.state.validation.phase_gates_prd import _check_prd_enforcement


def _check_plan_input(
    run_path: Path,
    config: TRWConfig,
    severity: str,
    failures: list[ValidationFailure],
) -> None:
    """Check PLAN phase input prerequisites."""
    del config  # config unused in this checker; kept for dispatch-table uniformity
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
    prd_failures = _check_prd_enforcement(run_path, config, PRDStatus.APPROVED, "implement")
    failures.extend(prd_failures)


def _check_validate_input(
    run_path: Path,
    config: TRWConfig,
    severity: str,
    failures: list[ValidationFailure],
) -> None:
    """Check VALIDATE phase input prerequisites."""
    del config
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
    del config
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
    del config
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


_InputChecker = Callable[[Path, TRWConfig, str, list[ValidationFailure]], None]

_INPUT_CHECKERS: dict[str, _InputChecker] = {
    "plan": _check_plan_input,
    "implement": _check_implement_input,
    "validate": _check_validate_input,
    "review": _check_review_input,
    "deliver": _check_deliver_input,
    # research: no per-phase prerequisites beyond run.yaml
}
