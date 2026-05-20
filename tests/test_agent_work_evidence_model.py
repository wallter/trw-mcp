"""Tests for AgentWorkEvidence v1 strict schema and integrity helpers."""

from __future__ import annotations

from trw_mcp.models.agent_work_evidence import (
    AgentInfo,
    AgentWorkEvidence,
    ArtifactEvidence,
    ChangedFileEvidence,
    EvidenceEventSummary,
    EvidenceIntegrity,
    EvidenceTimestamps,
    ReviewEvidence,
    RunIdentity,
    VerificationEvidence,
    compute_agent_work_evidence_integrity,
    validate_agent_work_evidence,
)


def _valid_evidence() -> AgentWorkEvidence:
    return AgentWorkEvidence(
        identity=RunIdentity(run_id="20260520T033353Z-abc123", run_path=".trw/runs/demo"),
        task="Implement PRD-CORE-168",
        prd_scope=["PRD-CORE-168"],
        phase="implement",
        status="active",
        agent=AgentInfo(agent_id="agent-1", role="implementer"),
        timestamps=EvidenceTimestamps(started_at="2026-05-20T03:33:53Z", generated_at="2026-05-20T03:40:00Z"),
        intent="Create judge-ingestible work evidence.",
        plan_summary="Define strict schema, assemble run state, expose MCP tool.",
        changed_files=[
            ChangedFileEvidence(
                id="changed-file:trw-mcp/src/trw_mcp/models/agent_work_evidence.py",
                path="trw-mcp/src/trw_mcp/models/agent_work_evidence.py",
                change_type="added",
                diff_hash="ab" * 32,
                related_prds=["PRD-CORE-168"],
                related_frs=["FR-1", "FR-7"],
            )
        ],
        verification=VerificationEvidence(
            id="verification:focused:pytest",
            status="passed",
            tests_passed=True,
            static_checks_clean=True,
            coverage_pct=91.5,
            failure_count=0,
            command="pytest focused",
            scope="focused",
        ),
        review=[
            ReviewEvidence(
                id="review:self:privacy",
                category="self",
                status="passed",
                summary="No raw diffs or transcripts are included.",
            )
        ],
        artifacts=[
            ArtifactEvidence(
                id="artifact:run:meta-run-yaml",
                category="run",
                path=".trw/runs/demo/meta/run.yaml",
                content_hash="cd" * 32,
            )
        ],
        event_summary=EvidenceEventSummary(total_count=2, by_type={"run_init": 1, "tests_passed": 1}),
        warnings=[],
    )


def test_agent_work_evidence_is_strict_v1_schema() -> None:
    """FR-1: strict v1 model includes required work-evidence sections."""
    evidence = _valid_evidence()

    dumped = evidence.model_dump()

    assert dumped["schema_version"] == "agent-work-evidence/v1"
    assert dumped["identity"]["run_id"] == "20260520T033353Z-abc123"
    assert dumped["prd_scope"] == ["PRD-CORE-168"]
    assert dumped["changed_files"][0]["related_frs"] == ["FR-1", "FR-7"]
    assert dumped["verification"]["status"] == "passed"
    assert dumped["event_summary"]["by_type"]["tests_passed"] == 1


def test_validate_agent_work_evidence_reports_extra_and_missing_fields() -> None:
    """FR-5: validation helper returns machine-readable strict-schema errors."""
    invalid = _valid_evidence().model_dump()
    invalid.pop("identity")
    invalid["unexpected"] = "not allowed"

    result = validate_agent_work_evidence(invalid)

    assert result.valid is False
    locations = {tuple(error.loc) for error in result.errors}
    assert ("identity",) in locations
    assert ("unexpected",) in locations
    assert {error.type for error in result.errors} >= {"missing", "extra_forbidden"}


def test_integrity_hash_is_deterministic_and_excludes_integrity_field() -> None:
    """FR-7: SHA-256 covers canonical JSON excluding the integrity field."""
    evidence = _valid_evidence()

    first = compute_agent_work_evidence_integrity(evidence)
    with_integrity = evidence.model_copy(update={"integrity": first})
    second = compute_agent_work_evidence_integrity(with_integrity)

    assert isinstance(first, EvidenceIntegrity)
    assert first.algorithm == "sha256"
    assert len(first.digest) == 64
    assert first == second


def test_integrity_changes_when_canonical_content_changes() -> None:
    """FR-7: canonical hash changes when evidence content changes."""
    evidence = _valid_evidence()

    original = compute_agent_work_evidence_integrity(evidence)
    changed = evidence.model_copy(update={"task": "Different task"})

    assert compute_agent_work_evidence_integrity(changed).digest != original.digest
