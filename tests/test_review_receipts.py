"""PRD-CORE-205 FR02/FR03 — ReviewReceipt substance + gate derivation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trw_mcp.models._evidence_core import ReceiptState
from trw_mcp.models._evidence_plans import ReviewVerdict
from trw_mcp.models._evidence_records import ReviewReceipt
from trw_mcp.tools._evidence_gates import validate_review_receipt
from trw_mcp.tools._review_manual import handle_manual_mode
from trw_mcp.tools._review_receipt_writer import load_latest_review_evidence
from trw_mcp.tools._review_reviewer_family import digest_external_receipt

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
        # Deliver-time plan resolved DIFFERENT governing bytes -> the plan digest (which covers them) mismatches first.
        other_plan = review_plan(binding, governing_digest="gov-B")
        result = validate_review_receipt(receipt, other_plan, project)
        assert not result.is_positive
        assert result.state is ReceiptState.INVALID
        assert result.reason_code == "review_plan_digest_mismatch"

    def test_input_digest_not_derived_from_this_plan_is_invalid(self, tmp_path: Path) -> None:
        """E2E-COV-SIGNOFF-RECEIPT: plan digest matches, but the input digest names other content -> INVALID.

        ``test_governing_byte_mismatch_is_invalid`` above stops at the plan-digest check, so this is the
        only test that reaches the input-digest refusal.
        """
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        plan = review_plan(binding)
        honest = review_receipt(binding, plan)
        forged = ReviewReceipt.model_validate_json(  # the on-disk load path
            json.dumps(
                {**honest.model_dump(mode="json"), "review_input_digest": honest.expected_input_digest("gov-other")}
            )
        )
        assert forged.review_plan_digest == plan.plan_digest  # precondition: only the input digest differs

        result = validate_review_receipt(forged, plan, project)
        assert not result.is_positive
        assert result.state is ReceiptState.INVALID
        assert result.reason_code == "review_input_digest_mismatch"

    @pytest.mark.parametrize("field", ["method", "reviewer_identity", "completed_at"])
    def test_receipt_missing_a_provenance_field_is_not_substantive(self, tmp_path: Path, field: str) -> None:
        """E2E-COV-SIGNOFF-RECEIPT: full plan coverage does not make a receipt with no method/reviewer/time count."""
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        plan = review_plan(binding)
        honest = review_receipt(binding, plan)
        hollow = ReviewReceipt.model_validate_json(json.dumps({**honest.model_dump(mode="json"), field: ""}))

        result = validate_review_receipt(hollow, plan, project)
        assert not result.is_positive
        assert result.state is ReceiptState.INVALID
        assert result.reason_code == "review_not_substantive"


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


# ---------------------------------------------------------------------------
# PRD-CORE-255-FR02 — reviewer_family is EARNED, never asserted
# ---------------------------------------------------------------------------


def _manual_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real project + run so ``handle_manual_mode`` writes a real receipt."""
    source = tmp_path / "src" / "feature.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    prd = tmp_path / "docs" / "requirements-aare-f" / "prds" / "PRD-CORE-255.md"
    prd.parent.mkdir(parents=True, exist_ok=True)
    prd.write_text("---\nprd:\n  id: PRD-CORE-255\n---\n\n# body\n", encoding="utf-8")
    run = tmp_path / ".trw" / "runs" / "task" / "run-1"
    (run / "meta").mkdir(parents=True, exist_ok=True)
    (run / "meta" / "events.jsonl").write_text(
        json.dumps({"event": "file_modified", "file": str(source)}) + "\n", encoding="utf-8"
    )
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    return run


def _external_artifact(tmp_path: Path, body: str = "agy findings: 3 P1s\n") -> tuple[Path, str]:
    artifact = tmp_path / "reports" / "agy-audit.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(body, encoding="utf-8")
    return artifact, hashlib.sha256(body.encode("utf-8")).hexdigest()


class TestManualModeReviewerFamilyIsDigestVerified:
    """FR02. Fails before: ``_reviewer_fields`` keyed family on the dispatch
    ``mode`` string alone, so a manual relay of a real agy/codex audit was
    stamped ``human_or_self`` (the mislabel observed this session — every review
    reported single_family though the cross-family auditors had in fact run).
    """

    def test_manual_mode_reviewer_source_drives_family_not_dispatch_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = _manual_project(tmp_path, monkeypatch)
        artifact, digest = _external_artifact(tmp_path)

        result = handle_manual_mode(
            [{"category": "security", "severity": "critical", "description": "unbounded read"}],
            run,
            "review-1",
            datetime.now(timezone.utc).isoformat(),
            ["PRD-CORE-255"],
            reviewer_source="cross_model",
            reviewer_receipt_id=digest,
            external_receipt_path=str(artifact.relative_to(tmp_path)),
        )

        assert result["typed_receipt_state"] == "written"
        _, receipt = load_latest_review_evidence(run, tmp_path)
        assert receipt is not None
        assert receipt.reviewer_family == "cross_model"
        assert receipt.external_receipt_digest == digest
        assert receipt.family_downgraded_reason == ""
        assert result["review_family_coverage"] == "cross_family"

    def test_unverified_cross_model_claim_is_downgraded_with_reason(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No artifact at all: the strongest label is not mintable by assertion."""
        run = _manual_project(tmp_path, monkeypatch)

        result = handle_manual_mode(
            [{"category": "security", "severity": "critical", "description": "unbounded read"}],
            run,
            "review-1",
            datetime.now(timezone.utc).isoformat(),
            ["PRD-CORE-255"],
            reviewer_source="cross_model",
            reviewer_receipt_id="deadbeef",
        )

        _, receipt = load_latest_review_evidence(run, tmp_path)
        assert receipt is not None
        assert receipt.reviewer_family == "human_or_self"
        assert receipt.external_receipt_digest == ""
        assert receipt.family_downgraded_reason == "external_receipt_path_missing"
        assert result["family_downgraded_reason"] == "external_receipt_path_missing"
        assert result.get("review_family_coverage") != "cross_family"

    def test_digest_mismatch_and_unreadable_path_each_name_their_own_reason(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = _manual_project(tmp_path, monkeypatch)
        artifact, digest = _external_artifact(tmp_path)

        mismatch = handle_manual_mode(
            [],
            run,
            "review-1",
            datetime.now(timezone.utc).isoformat(),
            ["PRD-CORE-255"],
            reviewer_source="cross_model",
            reviewer_receipt_id="0" * 64,
            external_receipt_path=str(artifact.relative_to(tmp_path)),
            review_completed=True,
        )
        assert mismatch["family_downgraded_reason"] == "external_receipt_digest_mismatch"

        unreadable = handle_manual_mode(
            [],
            run,
            "review-2",
            datetime.now(timezone.utc).isoformat(),
            ["PRD-CORE-255"],
            reviewer_source="cross_model",
            reviewer_receipt_id=digest,
            external_receipt_path="reports/nope.md",
            review_completed=True,
        )
        assert unreadable["family_downgraded_reason"] == "external_receipt_path_unreadable"

    def test_path_outside_the_project_root_is_unreadable_not_digested(self, tmp_path: Path) -> None:
        """Containment is checked after symlink resolution — no arbitrary file reads."""
        outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
        outside.write_text("secret\n", encoding="utf-8")
        project = tmp_path / "proj"
        project.mkdir(exist_ok=True)
        try:
            assert digest_external_receipt(str(outside), project) is None
        finally:
            outside.unlink()

    def test_no_reviewer_source_keeps_the_unchanged_manual_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """US-002 AC3 regression: an ordinary manual review is untouched by FR02."""
        run = _manual_project(tmp_path, monkeypatch)

        handle_manual_mode(
            [],
            run,
            "review-1",
            datetime.now(timezone.utc).isoformat(),
            ["PRD-CORE-255"],
            review_completed=True,
        )

        _, receipt = load_latest_review_evidence(run, tmp_path)
        assert receipt is not None
        assert receipt.reviewer_family == "human_or_self"
        assert receipt.family_downgraded_reason == ""


class TestConcurrentReceiptWrites:
    def test_concurrent_receipt_write_does_not_corrupt_prior_receipt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """NFR04: receipts are append-only under immutable, id-named filenames."""
        run = _manual_project(tmp_path, monkeypatch)
        first = handle_manual_mode(
            [], run, "review-1", datetime.now(timezone.utc).isoformat(), ["PRD-CORE-255"], review_completed=True
        )
        first_path = run / "meta" / "receipts" / "review" / f"{first['review_receipt_id']}.json"
        before = first_path.read_bytes()

        second = handle_manual_mode(
            [], run, "review-2", datetime.now(timezone.utc).isoformat(), ["PRD-CORE-255"], review_completed=True
        )

        assert second["review_receipt_id"] != first["review_receipt_id"]
        assert first_path.read_bytes() == before
        assert len(list((run / "meta" / "receipts" / "review").glob("*.json"))) == 2
