"""PRD-CORE-205 FR06 — VerificationReceipt is execution evidence, not status."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.models._evidence_core import ReceiptState
from trw_mcp.models._evidence_plans import VerificationOutcome
from trw_mcp.tools._evidence_gates import validate_verification_receipt

from ._evidence_factories import project_with_binding, verification_receipt


class TestVerificationReceiptIsExecutionEvidenceNotMappingOrStatus:
    def test_verification_receipt_is_execution_evidence_not_mapping_or_status(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        receipt = verification_receipt(binding, mapping_digest="map-v1", outcome=VerificationOutcome.PASS)
        result = validate_verification_receipt(receipt, "map-v1", project)
        assert result.is_positive and result.state is ReceiptState.VALID

    def test_changed_mapping_is_not_reused_silently(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        receipt = verification_receipt(binding, mapping_digest="map-v1")
        # Mapping changed to v2 after the receipt was produced.
        result = validate_verification_receipt(receipt, "map-v2", project)
        assert not result.is_positive
        assert result.state is ReceiptState.STALE_CONTENT

    def test_distinct_outcomes_preserved(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        for outcome in (VerificationOutcome.PASS, VerificationOutcome.FAIL, VerificationOutcome.INCONCLUSIVE):
            receipt = verification_receipt(binding, mapping_digest="m", outcome=outcome)
            assert receipt.outcome is outcome
            # A receipt validates as execution evidence regardless of outcome; the
            # outcome value is preserved for downstream aggregation.
            assert validate_verification_receipt(receipt, "m", project).is_positive

    def test_stale_content_invalidates_verification(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        receipt = verification_receipt(binding, mapping_digest="m")
        (project / "src" / "a.py").write_text("changed", encoding="utf-8")
        assert validate_verification_receipt(receipt, "m", project).state is ReceiptState.STALE_CONTENT

    def test_receipt_has_no_prd_lifecycle_fields(self) -> None:
        # FR06: the receipt model carries NO PRD status / functionality_level field.
        from trw_mcp.models._evidence_records import VerificationReceipt

        fields = set(VerificationReceipt.model_fields)
        for forbidden in ("status", "functionality_level", "prd_status", "fr_status"):
            assert forbidden not in fields
