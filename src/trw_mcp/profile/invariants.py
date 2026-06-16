"""Governance invariants — PRD-HPO-PROF-001 FR-9 / FR-15 / NFR-6.

Belongs to the ``trw_mcp.profile`` package facade. Re-exported there.

Invariants are governance rules a composed profile MUST satisfy. In v1 they
operate in **enforce (fail-closed) mode only** — there is NO lint/suggest
alternative (FR-15; a lint mode is a deferred Wave-3 PRD). ``run_invariants``
returns the list of violations; ``enforce_invariants`` raises
``InvariantViolationError`` when any violation exists.

v1 invariants (FR-9):
  (a) ``phase_enabled_set`` MUST include ``DELIVER`` when it is set at all.
  (b) ``review_threshold`` MUST NOT be ``NONE`` in a non-dev profile.
  (c) ``build_check_scope=none`` is forbidden unless the profile sets
      ``env=dev``.

# trw:intentional fail-closed governance gate — these checks must never be
# downgraded to warnings in v1; a gate-bypass at audit is a code bug, not a
# silent lint allowance (PRD §7.7, FR-15).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from trw_mcp.profile.model import Profile


class InvariantViolation(BaseModel):
    """A single governance-invariant failure."""

    model_config = ConfigDict(extra="forbid")

    invariant_id: str
    message: str
    offending_value: object | None = None


class InvariantViolationError(ValueError):
    """Raised by ``enforce_invariants`` when a profile violates a rule.

    Carries the structured violation list so callers can surface the
    invariant id and offending value (Behavior Switch Matrix row).
    """

    def __init__(self, violations: list[InvariantViolation]) -> None:
        self.violations = violations
        ids = ", ".join(v.invariant_id for v in violations)
        super().__init__(f"profile invariant(s) violated: {ids}")


def run_invariants(profile: Profile) -> list[InvariantViolation]:
    """Return all v1 invariant violations for ``profile`` (FR-9).

    Pure: never raises, never logs. ``enforce_invariants`` is the fail-closed
    wrapper. An ``env=dev`` profile is exempt from the prod-only rules (b)/(c).
    """
    violations: list[InvariantViolation] = []
    is_dev = profile.env == "dev"

    # (a) DELIVER must remain enabled whenever phase_enabled_set is declared.
    if profile.phase_enabled_set is not None and "DELIVER" not in profile.phase_enabled_set:
        violations.append(
            InvariantViolation(
                invariant_id="INV-PHASE-DELIVER",
                message="phase_enabled_set must include DELIVER",
                offending_value=list(profile.phase_enabled_set),
            )
        )

    # (b) review_threshold must not be NONE outside a dev profile.
    if profile.review_threshold == "NONE" and not is_dev:
        violations.append(
            InvariantViolation(
                invariant_id="INV-REVIEW-NONE-PROD",
                message="review_threshold=NONE is forbidden unless env=dev",
                offending_value=profile.review_threshold,
            )
        )

    # (c) build_check_scope=none requires env=dev.
    if profile.build_check_scope == "none" and not is_dev:
        violations.append(
            InvariantViolation(
                invariant_id="INV-BUILDCHECK-NONE-PROD",
                message="build_check_scope=none is forbidden unless env=dev",
                offending_value=profile.build_check_scope,
            )
        )

    return violations


def enforce_invariants(profile: Profile) -> None:
    """Raise ``InvariantViolationError`` if ``profile`` violates any v1 rule.

    Fail-closed (FR-15): there is no suppression / lint path in v1.
    """
    violations = run_invariants(profile)
    if violations:
        raise InvariantViolationError(violations)


__all__ = [
    "InvariantViolation",
    "InvariantViolationError",
    "enforce_invariants",
    "run_invariants",
]
