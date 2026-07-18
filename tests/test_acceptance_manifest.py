"""AcceptanceManifest store: raw-source binding, no write-back, no digest
feedback, atomic persistence (PRD-QUAL-120 P02/P03)."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state.acceptance_manifest import (
    BLOCKER_EXISTENCE_ONLY,
    BLOCKER_NO_RECEIPT,
    ReceiptEvidence,
    derive_manifest,
    load_manifest,
    manifest_path,
    persist_manifest,
    registry_view,
)

_PRD = """---
prd:
  id: PRD-CORE-001
  title: T
  status: approved
verification:
  mappings:
  - requirement_id: PRD-CORE-001-FR01
    acceptance_criteria: [c1]
    method: test
    evidence_artifact: tests/test_x.py::t1
    pass_condition: p
  - requirement_id: PRD-CORE-001-FR02
    acceptance_criteria: [c2]
    method: test
    evidence_artifact: tests/test_x.py::t2
    pass_condition: p
---
# PRD-CORE-001
"""


def _write_prd(tmp_path: Path) -> Path:
    prd = tmp_path / "prds" / "PRD-CORE-001.md"
    prd.parent.mkdir(parents=True)
    prd.write_text(_PRD, encoding="utf-8")
    return prd


def test_derivation_binds_raw_source_bytes_and_types_every_state(tmp_path: Path) -> None:
    prd = _write_prd(tmp_path)
    manifest = derive_manifest(
        prd,
        {
            "PRD-CORE-001-FR01": ReceiptEvidence("receipt-1", "sha256:" + "a" * 64),
            # FR02 gets an existence-only receipt (no content binding).
            "PRD-CORE-001-FR02": ReceiptEvidence("receipt-2", ""),
        },
    )
    assert manifest.prd_id == "PRD-CORE-001"
    assert manifest.source_digest.startswith("sha256:")
    by_id = {requirement.requirement_id: requirement for requirement in manifest.requirements}
    assert str(by_id["PRD-CORE-001-FR01"].state) == "accepted"
    assert str(by_id["PRD-CORE-001-FR02"].state) == "blocked"
    assert by_id["PRD-CORE-001-FR02"].blocker == BLOCKER_EXISTENCE_ONLY

    # Missing receipt -> unknown with typed blocker; never an implicit pass.
    bare = derive_manifest(prd, {})
    assert all(str(requirement.state) == "unknown" for requirement in bare.requirements)
    assert all(requirement.blocker == BLOCKER_NO_RECEIPT for requirement in bare.requirements)

    # Source binding: any byte change changes source_digest.
    prd.write_text(_PRD + "\n<!-- touched -->\n", encoding="utf-8")
    assert derive_manifest(prd, {}).source_digest != manifest.source_digest


def test_persist_load_roundtrip_and_tamper_rejection(tmp_path: Path) -> None:
    prd = _write_prd(tmp_path)
    trw = tmp_path / ".trw"
    manifest = derive_manifest(prd, {})
    target = persist_manifest(manifest, trw)
    assert target == manifest_path(trw, "PRD-CORE-001")
    loaded = load_manifest(trw, "PRD-CORE-001")
    assert loaded is not None
    assert loaded.canonical_digest() == manifest.canonical_digest()

    # Tampered payload is a typed absence, never a pass.
    target.write_text(target.read_text(encoding="utf-8").replace("unknown", "accepted"), encoding="utf-8")
    assert load_manifest(trw, "PRD-CORE-001") is None


def test_no_write_back_and_no_digest_feedback(tmp_path: Path) -> None:
    """FR03: deriving + persisting never touches the authored PRD bytes, and
    the manifest digest is not an input to its own derivation."""
    prd = _write_prd(tmp_path)
    before = prd.read_bytes()
    trw = tmp_path / ".trw"

    first = derive_manifest(prd, {})
    persist_manifest(first, trw)
    second = derive_manifest(prd, {})

    assert prd.read_bytes() == before  # no write-back into authored bytes
    # No feedback loop: deriving again after persistence yields identical
    # canonical bytes (the stored manifest digest did not become an input).
    assert second.canonical_digest() == first.canonical_digest()
    # And nothing was written outside the manifest directory.
    written = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert all(".trw" in path.parts or path == prd for path in written), written


def test_registry_view_is_read_only_and_deterministic(tmp_path: Path) -> None:
    prd = _write_prd(tmp_path)
    manifest = derive_manifest(prd, {"PRD-CORE-001-FR01": ReceiptEvidence("r", "sha256:" + "b" * 64)})
    view = registry_view(manifest)
    assert (view.accepted_count, view.blocked_count, view.unknown_count) == (1, 0, 1)
    assert view.manifest_digest == manifest.canonical_digest()
    # Frozen value object: mutation is impossible (read-only adapter).
    import dataclasses

    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        view.accepted_count = 5  # type: ignore[misc]


def test_completion_outcome_vocabulary_rejected(tmp_path: Path) -> None:
    """Audit F8 rejection path: a completion_outcome outside the
    EffectiveCompletionOutcome vocabulary is refused at model construction."""
    import pytest

    from trw_mcp.models.requirements import AcceptanceManifest

    with pytest.raises(Exception, match="completion_outcome must be one of"):
        AcceptanceManifest(prd_id="PRD-X", source_digest="sha256:" + "a" * 64, completion_outcome="bogus_value")
