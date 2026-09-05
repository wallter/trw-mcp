"""Typed validation findings: verification-command lint + the valid/error invariant.

Two closely-related truthfulness rules live here because both decide whether a
``ValidationResult``'s ``valid`` flag is *explainable*:

* :func:`verification_command_failures` — PRD-INFRA-179-FR02. A
  ``verification_commands`` entry that is not a runnable invocation becomes an
  ``error``-severity finding, so it is caught at validate time instead of at
  ``bash -c`` time as a generic exit-127 tail.
* :func:`enforce_valid_invariant` — every ``valid=False`` verdict must carry at
  least one ``error``-severity finding naming the rule that produced it. A
  ``valid=False`` with only warnings is a fail-silent contradiction: the caller
  is told the PRD failed and given nothing to fix.

Split out of ``_prd_validation.py``/``prd_quality.py`` to keep both under the
350 effective-LOC gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from trw_mcp.models.requirements import PRDQualityGates, ValidationFailure
from trw_mcp.state.validation._verification_command_lint import malformed_verification_commands

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Rule name attached to malformed-``verification_commands`` findings. Callers
#: (and ``scripts/prd_verify_check.py``'s report) key on this string.
VERIFICATION_COMMAND_RULE = "verification_command_runnable"

#: Rule name for the backstop finding emitted when some gate flipped ``valid``
#: without naming itself. Seeing this in output is a validator defect, not a
#: PRD defect — it means a gate needs to emit its own error-severity finding.
VALID_WITHOUT_ERROR_RULE = "valid_without_error_finding"


class _ValidationResultLike(Protocol):
    """Minimal surface both ``ValidationResult`` and ``ValidationResultV2`` share."""

    valid: bool
    failures: list[ValidationFailure]


def verification_command_failures(
    frontmatter: dict[str, object],
    *,
    repo_root: Path | None = None,
) -> list[ValidationFailure]:
    """Return an ``error`` finding per non-runnable ``verification_commands`` entry."""
    raw = frontmatter.get("verification_commands")
    if not isinstance(raw, list) or not raw:
        return []
    return [
        ValidationFailure(
            field="verification_commands",
            rule=VERIFICATION_COMMAND_RULE,
            message=(f"malformed verification_commands entry (not a runnable command): {reason}. Entry: {entry!r}"),
            severity="error",
        )
        for entry, reason in malformed_verification_commands(list(raw), repo_root=repo_root)
    ]


def has_blocking_failure(failures: Sequence[ValidationFailure]) -> bool:
    """True when at least one failure is ``error`` severity (warnings never block)."""
    return any(failure.severity == "error" for failure in failures)


def quality_gate_failures(
    *,
    is_valid: bool,
    completeness: float,
    trace_coverage: float,
    gates: PRDQualityGates,
) -> list[ValidationFailure]:
    """Name the numeric quality gate a ``valid=False`` V1 verdict rests on.

    Without this, a PRD with no traceability links flipped ``valid`` to False
    carrying only a *warning* ("PRD has no traceability links") — a verdict with
    no error to act on. Returns ``[]`` when the verdict is valid, or when every
    numeric gate is met (an ``error``-severity finding already explains it).
    """
    if is_valid:
        return []
    unmet: list[str] = []
    if completeness < gates.completeness_min:
        unmet.append(f"completeness {completeness:.2f} < {gates.completeness_min:.2f}")
    if trace_coverage < gates.traceability_coverage_min:
        unmet.append(f"traceability_coverage {trace_coverage:.2f} < {gates.traceability_coverage_min:.2f}")
    if not unmet:
        return []
    return [
        ValidationFailure(
            field="quality_gates",
            rule="quality_gate_threshold",
            message="PRD does not meet its quality gates: " + "; ".join(unmet),
            severity="error",
        )
    ]


def enforce_valid_invariant(result: _ValidationResultLike) -> None:
    """Ensure a ``valid=False`` result names at least one ``error``-severity rule.

    Mutates ``result.failures`` in place, appending a backstop finding when some
    gate flipped ``valid`` without emitting an error. Never changes ``valid``.
    """
    if result.valid or has_blocking_failure(result.failures):
        return
    warning_rules = sorted({failure.rule for failure in result.failures}) or ["<no findings at all>"]
    result.failures = [
        *result.failures,
        ValidationFailure(
            field="valid",
            rule=VALID_WITHOUT_ERROR_RULE,
            message=(
                "validator defect: valid=False was produced with no error-severity finding; "
                f"only these non-blocking rules were recorded: {', '.join(warning_rules)}. "
                "The gate that rejected this PRD must emit its own error-severity finding."
            ),
            severity="error",
        ),
    ]
