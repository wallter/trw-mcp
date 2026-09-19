"""CORE-205 FR06: named evidence bytes remain current independently of source."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from trw_mcp.models._evidence_core import ReceiptState
from trw_mcp.models._evidence_plans import VerificationOutcome
from trw_mcp.models._evidence_records import VerificationReceipt
from trw_mcp.state._evidence_binding import content_binding_is_current
from trw_mcp.state._evidence_gates import validate_verification_receipt
from trw_mcp.state._evidence_persistence import canonical_receipt_bytes, write_receipt
from trw_mcp.state._trust_receipts import collect_positive_trust_evidence

from ._evidence_factories import project_with_binding, verification_receipt


@pytest.fixture
def named_artifact_receipt(tmp_path: Path) -> tuple[Path, VerificationReceipt, Path]:
    project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "unchanged source"})
    # Evidence is intentionally outside the source binding: FR06 separately
    # requires the named artifact's bytes, not just unchanged verified source.
    artifact = project / "proof.txt"
    artifact.write_bytes(b"PASS: executed verification evidence\n")
    receipt = VerificationReceipt(
        **{
            **verification_receipt(binding, mapping_digest="map-v1").model_dump(),
            "evidence_artifact_path": "proof.txt",
            "evidence_artifact_digest": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        }
    )
    assert write_receipt(project / "run", "verification", receipt.receipt_id, receipt).ok
    return project, receipt, artifact


def test_current_named_artifact_validates_and_contributes_persisted_trust(
    named_artifact_receipt: tuple[Path, VerificationReceipt, Path],
) -> None:
    project, receipt, _ = named_artifact_receipt
    result = validate_verification_receipt(receipt, "map-v1", project)
    assert result.state is ReceiptState.VALID
    assert result.is_positive
    kinds, contributing = collect_positive_trust_evidence(project / "run", project)
    assert kinds == {"verification"}
    assert contributing == [(receipt.receipt_id, hashlib.sha256(canonical_receipt_bytes(receipt)).hexdigest())]


@pytest.mark.parametrize("artifact_change", ["mutate", "delete"])
@pytest.mark.parametrize("consumer", ["validation", "persisted_trust"])
def test_changed_named_artifact_is_nonpositive_with_source_and_mapping_unchanged(
    named_artifact_receipt: tuple[Path, VerificationReceipt, Path],
    artifact_change: str,
    consumer: str,
) -> None:
    project, receipt, artifact = named_artifact_receipt
    # Establish both positive controls before changing only the named evidence.
    assert validate_verification_receipt(receipt, "map-v1", project).is_positive
    assert collect_positive_trust_evidence(project / "run", project)[0] == {"verification"}
    if artifact_change == "mutate":
        artifact.write_bytes(b"FAIL: evidence changed after receipt persistence\n")
    else:
        artifact.unlink()

    assert content_binding_is_current(receipt.content_binding, project).state is ReceiptState.VALID
    if consumer == "validation":
        assert not validate_verification_receipt(receipt, "map-v1", project).is_positive
    else:
        kinds, contributing = collect_positive_trust_evidence(project / "run", project)
        assert kinds == set()
        assert contributing == []


@pytest.mark.parametrize("outcome", list(VerificationOutcome))
def test_current_artifact_preserves_all_outcomes_but_only_pass_contributes_trust(
    tmp_path: Path, outcome: VerificationOutcome
) -> None:
    project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "unchanged"})
    receipt = verification_receipt(binding, outcome=outcome, project_root=project)
    assert write_receipt(project / "run", "verification", receipt.receipt_id, receipt).ok
    result = validate_verification_receipt(receipt, "map1", project)
    assert result.state is ReceiptState.VALID
    assert result.is_positive
    assert receipt.outcome is outcome
    kinds, contributing = collect_positive_trust_evidence(project / "run", project)
    if outcome is VerificationOutcome.PASS:
        assert kinds == {"verification"}
        assert contributing == [(receipt.receipt_id, hashlib.sha256(canonical_receipt_bytes(receipt)).hexdigest())]
    else:
        assert kinds == set()
        assert contributing == []


@pytest.mark.parametrize("empty_field", ["both", "evidence_artifact_path", "evidence_artifact_digest"])
def test_legacy_empty_artifact_metadata_is_parseable_but_nonpositive(tmp_path: Path, empty_field: str) -> None:
    project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "unchanged"})
    receipt = verification_receipt(binding, project_root=project if empty_field != "both" else None)
    payload = receipt.model_dump()
    if empty_field != "both":
        payload[empty_field] = ""
    receipt = VerificationReceipt(**payload)
    assert VerificationReceipt.model_validate_json(receipt.model_dump_json()) == receipt
    assert write_receipt(project / "run", "verification", receipt.receipt_id, receipt).ok
    assert not validate_verification_receipt(receipt, "map1", project).is_positive
    assert collect_positive_trust_evidence(project / "run", project) == (set(), [])


def test_validation_and_collection_do_not_mutate_requirement_lifecycle(
    named_artifact_receipt: tuple[Path, VerificationReceipt, Path],
) -> None:
    project, receipt, artifact = named_artifact_receipt
    prd = project / "PRD-CORE-205.md"
    lifecycle = b"---\nstatus: draft\nfunctionality_level: stub\n---\n# FR06\n"
    prd.write_bytes(lifecycle)
    before = receipt.model_dump_json()
    for current in (True, False):
        if not current:
            artifact.unlink()
        validate_verification_receipt(receipt, "map-v1", project)
        collect_positive_trust_evidence(project / "run", project)
        assert prd.read_bytes() == lifecycle
        assert receipt.model_dump_json() == before
