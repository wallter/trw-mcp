"""PRD-CORE-205 FR02/FR03 — ReviewReceipt substance + gate derivation."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.models._evidence_core import ReceiptState
from trw_mcp.models._evidence_plans import ReviewVerdict
from trw_mcp.tools._evidence_gates import select_typed_review_state, validate_review_receipt

from ._evidence_factories import project_with_binding, review_plan, review_receipt


class TestCleanZeroFindingReviewReceiptIsSubstantive:
    def test_clean_zero_finding_review_receipt_is_substantive(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        plan = review_plan(binding)
        receipt = review_receipt(binding, plan, verdict=ReviewVerdict.PASS)
        assert receipt.findings == ()  # honest zero-finding review
        result = validate_review_receipt(receipt, plan, project)
        assert result.is_positive
        assert result.state is ReceiptState.VALID

    def test_warned_and_blocked_reviews_are_substantive(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        plan = review_plan(binding)
        for verdict in (ReviewVerdict.WARN, ReviewVerdict.BLOCK):
            receipt = review_receipt(binding, plan, verdict=verdict)
            assert validate_review_receipt(receipt, plan, project).is_positive

    def test_missing_rubric_coverage_is_non_substantive(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        plan = review_plan(binding, rubric_ids=("R1", "R2", "R3"))
        # Realized only R1/R2 -> missing R3.
        receipt = review_receipt(binding, plan, realized_rubric_ids=("R1", "R2"))
        result = validate_review_receipt(receipt, plan, project)
        assert not result.is_positive
        assert result.state is ReceiptState.PLAN_INCOMPLETE

    def test_fabricated_role_does_not_stamp_all_roles(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        plan = review_plan(binding, roles=("independent", "security"))
        receipt = review_receipt(binding, plan, realized_roles=("independent",))
        assert validate_review_receipt(receipt, plan, project).state is ReceiptState.PLAN_INCOMPLETE

    def test_degraded_receipt_is_not_substantive(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        plan = review_plan(binding)
        receipt = review_receipt(binding, plan, degraded_reason="provider unavailable, pattern-scan only")
        assert validate_review_receipt(receipt, plan, project).state is ReceiptState.DEGRADED

    def test_governing_byte_mismatch_is_invalid(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        plan = review_plan(binding, governing_digest="gov-A")
        receipt = review_receipt(binding, plan)
        # Deliver-time plan resolved DIFFERENT governing bytes -> input digest mismatch.
        other_plan = review_plan(binding, governing_digest="gov-B")
        result = validate_review_receipt(receipt, other_plan, project)
        assert not result.is_positive
        assert result.state is ReceiptState.INVALID


class TestReviewGateDerivesSubstanceFromCurrentReceipt:
    def test_review_gate_derives_substance_from_current_receipt(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        plan = review_plan(binding)
        receipt = review_receipt(binding, plan)
        assert validate_review_receipt(receipt, plan, project).is_positive
        # One bound byte changes -> stale, no longer substantive.
        (project / "src" / "a.py").write_text("CHANGED", encoding="utf-8")
        result = validate_review_receipt(receipt, plan, project)
        assert result.state is ReceiptState.STALE_CONTENT
        assert not result.is_positive


class TestTypedPresentNoFallback:
    def test_typed_absent_allows_legacy(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        (run / "meta").mkdir(parents=True)
        result = select_typed_review_state(run)
        assert result.state is ReceiptState.LEGACY_UNBOUND
        assert result.typed_present is False

    def test_typed_present_malformed_blocks_legacy(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        rdir = run / "meta" / "receipts" / "review"
        rdir.mkdir(parents=True)
        (rdir / "review-bad.json").write_text('{"broken": true}', encoding="utf-8")
        result = select_typed_review_state(run)
        assert result.typed_present is True
        assert not result.is_positive
        assert result.state is ReceiptState.INVALID
