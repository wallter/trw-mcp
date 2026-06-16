"""FR-9 / FR-15 — governance invariant tests (fail-closed, no lint mode).

Each v1 invariant has a dedicated negative test, plus a proof that invariants
always enforce (no lint/suggest path exists in the v1 API).
"""

from __future__ import annotations

import pytest

from trw_mcp.profile import (
    InvariantViolationError,
    Profile,
    ProfileLayer,
    compose,
    enforce_invariants,
    run_invariants,
)


def test_invariant_deliver_required() -> None:
    """FR-9 (a): phase_enabled_set without DELIVER is a violation."""
    violations = run_invariants(Profile(phase_enabled_set=["IMPLEMENT"]))
    ids = {v.invariant_id for v in violations}
    assert "INV-PHASE-DELIVER" in ids


def test_invariant_deliver_present_passes() -> None:
    """FR-9 (a): including DELIVER clears the invariant."""
    violations = run_invariants(Profile(phase_enabled_set=["IMPLEMENT", "DELIVER"]))
    assert not violations


def test_invariant_review_threshold_non_none_in_prod() -> None:
    """FR-9 (b): review_threshold=NONE is forbidden outside env=dev."""
    violations = run_invariants(Profile(review_threshold="NONE"))
    assert any(v.invariant_id == "INV-REVIEW-NONE-PROD" for v in violations)


def test_invariant_review_none_allowed_in_dev() -> None:
    """FR-9 (b): env=dev exempts review_threshold=NONE."""
    violations = run_invariants(Profile(review_threshold="NONE", env="dev"))
    assert not any(v.invariant_id == "INV-REVIEW-NONE-PROD" for v in violations)


def test_invariant_build_check_none_requires_env_dev() -> None:
    """FR-9 (c): build_check_scope=none requires env=dev."""
    prod = run_invariants(Profile(build_check_scope="none"))
    assert any(v.invariant_id == "INV-BUILDCHECK-NONE-PROD" for v in prod)
    dev = run_invariants(Profile(build_check_scope="none", env="dev"))
    assert not any(v.invariant_id == "INV-BUILDCHECK-NONE-PROD" for v in dev)


def test_invariants_always_enforce_in_v1() -> None:
    """FR-15: enforce_invariants raises on any violation (fail-closed)."""
    with pytest.raises(InvariantViolationError):
        enforce_invariants(Profile(review_threshold="NONE"))


def test_no_lint_mode_exposed_in_v1_api() -> None:
    """FR-15: there is no lint/suggest parameter on the enforcement API.

    The public surface offers only run_invariants (returns list) and
    enforce_invariants (raises). No mode/severity/lint kwarg exists that could
    downgrade a violation to a warning.
    """
    import inspect

    sig = inspect.signature(enforce_invariants)
    assert list(sig.parameters) == ["profile"]
    for forbidden in ("mode", "lint", "suggest", "severity", "warn"):
        assert forbidden not in sig.parameters


def test_invariant_violation_aborts_resolution() -> None:
    """FR-12/FR-15: a violating composed profile aborts compose()."""
    layer = ProfileLayer(name="org", overrides=Profile(review_threshold="NONE"))
    with pytest.raises(InvariantViolationError):
        compose([layer])
