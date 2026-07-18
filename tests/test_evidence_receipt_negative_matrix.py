"""PRD-CORE-205 NFR01 — every malformed/stale/incomplete input fails toward no-evidence."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models._evidence_core import ReceiptState
from trw_mcp.models._evidence_plans import ReviewVerdict
from trw_mcp.tools._evidence_gates import (
    validate_build_receipt,
    validate_review_receipt,
    validate_verification_receipt,
)

from ._evidence_factories import (
    build_receipt,
    project_with_binding,
    review_plan,
    review_receipt,
    validation_plan,
    verification_receipt,
)


class TestInvalidReceiptsFailTowardNoEvidence:
    """No negative fixture returns pass/substantive."""

    def test_review_negative_fixtures(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        plan = review_plan(binding, rubric_ids=("R1", "R2"), roles=("independent",))

        cases = [
            # (mutator, expected non-positive state)
            (review_receipt(binding, plan, degraded_reason="degraded"), ReceiptState.DEGRADED),
            (review_receipt(binding, plan, realized_rubric_ids=("R1",)), ReceiptState.PLAN_INCOMPLETE),
            (review_receipt(binding, plan, realized_roles=()), ReceiptState.PLAN_INCOMPLETE),
        ]
        for receipt, expected in cases:
            result = validate_review_receipt(receipt, plan, project)
            assert not result.is_positive
            assert result.state is expected

    def test_review_stale_after_mutation(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        plan = review_plan(binding)
        receipt = review_receipt(binding, plan, verdict=ReviewVerdict.PASS)
        (project / "src" / "a.py").write_text("x", encoding="utf-8")
        assert not validate_review_receipt(receipt, plan, project).is_positive

    def test_build_negative_fixtures(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        plan = validation_plan(binding, required_command_ids=("pytest", "mypy"))
        from ._evidence_factories import build_command

        # Missing required command.
        r1 = build_receipt(binding, plan, command_results=(build_command("pytest"),))
        assert validate_build_receipt(r1, plan, project).state is ReceiptState.PLAN_INCOMPLETE
        # Nonzero required exit.
        r2 = build_receipt(
            binding,
            plan,
            command_results=(build_command("pytest", exit_code=2), build_command("mypy")),
        )
        assert not validate_build_receipt(r2, plan, project).is_positive
        # Contradictory legacy boolean.
        r3 = build_receipt(
            binding, validation_plan(binding, required_command_ids=("pytest",)), legacy_tests_passed=False
        )
        assert not validate_build_receipt(
            r3, validation_plan(binding, required_command_ids=("pytest",)), project
        ).is_positive

    def test_verification_negative_fixtures(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        receipt = verification_receipt(binding, mapping_digest="m1")
        # Changed mapping.
        assert not validate_verification_receipt(receipt, "m2", project).is_positive
        # Stale content.
        (project / "src" / "a.py").write_text("y", encoding="utf-8")
        assert not validate_verification_receipt(receipt, "m1", project).is_positive

    @pytest.mark.parametrize("state", list(ReceiptState))
    def test_only_valid_state_is_ever_positive(self, state: ReceiptState) -> None:
        from trw_mcp.models._evidence_core import ReceiptValidationResult

        result = ReceiptValidationResult(state=state, reason_code="x")
        assert result.is_positive == (state is ReceiptState.VALID)
