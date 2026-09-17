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
from typing import TYPE_CHECKING, Protocol, runtime_checkable

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


#: The verdict vocabulary. Two values, because readiness is a decision, not a
#: band: a caller either may implement this PRD or must fix something first.
VERDICT_READY = "READY"
VERDICT_NEEDS_WORK = "NEEDS_WORK"

#: Prefix ``_prd_quality_refresh`` puts on the integrity warning that discloses
#: a partial validation (fast mode, budget exhaustion, or a failed check group).
PARTIAL_MARKER_PREFIX = "validation_partial:"


@runtime_checkable
class _VerdictCarrier(Protocol):
    """The V2-only surface :func:`finalize_verdict` writes.

    Runtime-checkable so the shared V1/V2 entry point can tell the two apart
    without an ``hasattr`` dance mypy cannot narrow.
    """

    valid: bool
    failures: list[ValidationFailure]
    integrity_warnings: list[str]
    quality_tier: object
    grade: str
    total_score: float
    verdict: str
    verdict_note: str


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


def _blocking_rules(failures: Sequence[ValidationFailure]) -> list[str]:
    """Distinct rule names of the ``error``-severity findings, in report order."""
    return list(dict.fromkeys(failure.rule for failure in failures if failure.severity == "error"))


def finalize_verdict(result: _ValidationResultLike) -> None:
    """Enforce the valid/error invariant, then derive the ONE readiness verdict.

    PRD-FIX-141-FR09. ``trw_prd_validate`` used to answer the readiness question
    twice and differently: ``total_score: 91.87``, ``quality_tier: approved``,
    ``grade: A`` — and ``valid: false`` with three error-severity failures, for
    the same PRD in the same payload (learning L-9GXR). Both halves were
    internally correct. A score band is not a readiness decision, and nothing in
    the payload said which one to act on.

    ``verdict`` is derived from the RULES, with the score demoted to a secondary
    signal it never claims to be more than:

    * ``READY`` — ``valid`` and the validation was complete.
    * ``NEEDS_WORK`` — a rule blocks it, OR the run was PARTIAL. A partial run
      (fast mode, budget exhaustion, a failed check group) leaves grounding
      checks unperformed, so ``valid: true`` there means "nothing we ran
      objected", which is not the same claim and must not be promoted to one.

    ``verdict_note`` is non-empty for every ``NEEDS_WORK`` and names what blocks
    it — including, explicitly, when an approving tier or grade sits beside it,
    because that pairing is the one a reader is most likely to misread.

    Combined with :func:`enforce_valid_invariant` in ONE call because the two
    must not be done separately: a verdict derived before the invariant backstop
    could name no rule at all. Both the offline scorer and the dynamic-refresh
    path call this, so they cannot report different readiness for one PRD.
    """
    enforce_valid_invariant(result)
    if not isinstance(result, _VerdictCarrier):  # ValidationResult (V1) carries no verdict
        return

    warnings = list(getattr(result, "integrity_warnings", []))
    partial_markers = [w for w in warnings if str(w).startswith(PARTIAL_MARKER_PREFIX)]
    blocking = _blocking_rules(result.failures)

    if result.valid and not partial_markers:
        result.verdict = VERDICT_READY
        result.verdict_note = ""
        return

    result.verdict = VERDICT_NEEDS_WORK
    reasons: list[str] = []
    if blocking:
        reasons.append(f"blocked by: {', '.join(blocking)}")
    if partial_markers:
        reasons.append("validation was PARTIAL — some grounding checks did not run")
    if not reasons:
        reasons.append("the validator rejected this PRD without naming a rule")

    tier = str(getattr(getattr(result, "quality_tier", ""), "value", getattr(result, "quality_tier", "")))
    grade = str(getattr(result, "grade", ""))
    score_note = ""
    if tier == "approved" or grade == "A":
        score_note = (
            f" The score band (quality_tier={tier}, grade={grade}, total_score="
            f"{getattr(result, 'total_score', 0.0)}) describes WRITING QUALITY, not readiness; "
            "the verdict above is the one to act on."
        )
    result.verdict_note = "NEEDS_WORK: " + "; ".join(reasons) + "." + score_note
