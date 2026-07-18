"""PRD-CORE-205 FR08/NFR05 — observe/enforce mode + legacy reader matrix."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.models._evidence_core import EvidenceMode, ReceiptState
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._evidence_gates import (
    read_evidence_mode,
    select_typed_review_state,
    validate_review_receipt,
)

from ._evidence_factories import project_with_binding, review_plan, review_receipt


class TestObserveAndEnforceModesHaveExplicitLegacySemantics:
    def test_default_is_enforce_after_compatibility_closure(self) -> None:
        assert read_evidence_mode(TRWConfig()) is EvidenceMode.ENFORCE

    def test_enforce_is_explicit_and_reversible(self) -> None:
        assert read_evidence_mode(TRWConfig(evidence_receipt_mode="enforce")) is EvidenceMode.ENFORCE
        # Rollback to observe is configuration-only.
        assert read_evidence_mode(TRWConfig(evidence_receipt_mode="observe")) is EvidenceMode.OBSERVE

    def test_unknown_mode_is_non_positive(self) -> None:
        class Bad:
            evidence_receipt_mode = "surprise"

        assert read_evidence_mode(Bad()) is None

    def test_config_read_failure_is_non_positive(self) -> None:
        class Raises:
            @property
            def evidence_receipt_mode(self) -> str:
                raise RuntimeError("config unreadable")

        assert read_evidence_mode(Raises()) is None


class TestLegacyAndV1ReceiptReaderMatrix:
    def test_typed_absent_reports_legacy_unbound(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        (run / "meta").mkdir(parents=True)
        result = select_typed_review_state(run)
        assert result.state is ReceiptState.LEGACY_UNBOUND
        assert result.typed_present is False

    def test_typed_present_invalid_never_consults_legacy(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        rdir = run / "meta" / "receipts" / "review"
        rdir.mkdir(parents=True)
        (rdir / "review-xyz.json").write_text("not even json", encoding="utf-8")
        result = select_typed_review_state(run)
        # typed_present blocks legacy fallback in BOTH observe and enforce modes.
        assert result.typed_present is True
        assert not result.is_positive

    def test_valid_typed_receipt_is_positive_evidence(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        plan = review_plan(binding)
        receipt = review_receipt(binding, plan)
        # A fully validated current receipt is positive regardless of mode.
        assert validate_review_receipt(receipt, plan, project).is_positive
