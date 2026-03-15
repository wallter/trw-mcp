"""Tests for Sprint 44 model changes — ReviewFinding new fields + IntegrationReviewArtifact.

Coverage:
- ReviewFinding defaults for confidence, reviewer_role, evidence (INFRA-028-FR03/FR04)
- ReviewFinding with all fields set
- ReviewFinding.confidence bounds validation
- IntegrationReviewArtifact creation and validation (INFRA-027-FR03)
- IntegrationReviewArtifact verdict literal values
- IntegrationReviewArtifact with findings list
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trw_mcp.models.run import IntegrationReviewArtifact, ReviewFinding


class TestReviewFindingNewFields:
    """Tests for new ReviewFinding fields added in Sprint 44."""

    def test_review_finding_confidence_default(self) -> None:
        finding = ReviewFinding(category="quality", severity="warning", description="test")
        assert finding.confidence == 1.0

    def test_review_finding_reviewer_role_default(self) -> None:
        finding = ReviewFinding(category="quality", severity="warning", description="test")
        assert finding.reviewer_role == ""

    def test_review_finding_evidence_default(self) -> None:
        finding = ReviewFinding(category="quality", severity="warning", description="test")
        assert finding.evidence == ""

    def test_review_finding_all_fields(self) -> None:
        finding = ReviewFinding(
            category="security",
            severity="critical",
            description="SQL injection risk",
            file_path="app/main.py",
            suggestion="Use parameterized queries",
            confidence=0.95,
            reviewer_role="security",
            evidence="Line 42 uses string interpolation in query",
        )
        assert finding.category == "security"
        assert finding.severity == "critical"
        assert finding.description == "SQL injection risk"
        assert finding.file_path == "app/main.py"
        assert finding.suggestion == "Use parameterized queries"
        assert finding.confidence == 0.95
        assert finding.reviewer_role == "security"
        assert finding.evidence == "Line 42 uses string interpolation in query"

    def test_review_finding_confidence_at_zero(self) -> None:
        finding = ReviewFinding(category="quality", severity="info", description="test", confidence=0.0)
        assert finding.confidence == 0.0

    def test_review_finding_confidence_at_one(self) -> None:
        finding = ReviewFinding(category="quality", severity="info", description="test", confidence=1.0)
        assert finding.confidence == 1.0

    def test_review_finding_confidence_below_zero_invalid(self) -> None:
        with pytest.raises(ValidationError):
            ReviewFinding(category="quality", severity="info", description="test", confidence=-0.1)

    def test_review_finding_confidence_above_one_invalid(self) -> None:
        with pytest.raises(ValidationError):
            ReviewFinding(category="quality", severity="info", description="test", confidence=1.1)

    def test_review_finding_is_frozen(self) -> None:
        finding = ReviewFinding(category="quality", severity="warning", description="test")
        with pytest.raises(ValidationError):
            finding.confidence = 0.5  # type: ignore[misc]


class TestIntegrationReviewArtifact:
    """Tests for IntegrationReviewArtifact model (INFRA-027-FR03)."""

    def test_integration_review_artifact_creation(self) -> None:
        artifact = IntegrationReviewArtifact(
            run_id="run-abc123",
            reviewer_id="agent-001",
            reviewer_role="integration",
            timestamp="2026-03-03T05:00:00Z",
            git_diff_hash="abc123def456",
            shards_reviewed=["shard-01", "shard-02"],
            checks_performed=["duplicate_functions", "inconsistent_types"],
            findings=[],
            verdict="pass",
            human_escalation_path="Escalate to team lead via GitHub PR comment",
        )
        assert artifact.run_id == "run-abc123"
        assert artifact.reviewer_id == "agent-001"
        assert artifact.reviewer_role == "integration"
        assert artifact.verdict == "pass"
        assert artifact.shards_reviewed == ["shard-01", "shard-02"]

    def test_integration_review_artifact_verdict_pass(self) -> None:
        artifact = IntegrationReviewArtifact(
            run_id="r",
            reviewer_id="",
            reviewer_role="integration",
            timestamp="2026-03-03T00:00:00Z",
            git_diff_hash="",
            shards_reviewed=[],
            checks_performed=[],
            findings=[],
            verdict="pass",
            human_escalation_path="",
        )
        assert artifact.verdict == "pass"

    def test_integration_review_artifact_verdict_warn(self) -> None:
        artifact = IntegrationReviewArtifact(
            run_id="r",
            reviewer_id="",
            reviewer_role="integration",
            timestamp="2026-03-03T00:00:00Z",
            git_diff_hash="",
            shards_reviewed=[],
            checks_performed=[],
            findings=[],
            verdict="warn",
            human_escalation_path="",
        )
        assert artifact.verdict == "warn"

    def test_integration_review_artifact_verdict_block(self) -> None:
        artifact = IntegrationReviewArtifact(
            run_id="r",
            reviewer_id="",
            reviewer_role="integration",
            timestamp="2026-03-03T00:00:00Z",
            git_diff_hash="",
            shards_reviewed=[],
            checks_performed=[],
            findings=[],
            verdict="block",
            human_escalation_path="",
        )
        assert artifact.verdict == "block"

    def test_integration_review_artifact_invalid_verdict(self) -> None:
        with pytest.raises(ValidationError):
            IntegrationReviewArtifact(
                run_id="r",
                reviewer_id="",
                reviewer_role="integration",
                timestamp="2026-03-03T00:00:00Z",
                git_diff_hash="",
                shards_reviewed=[],
                checks_performed=[],
                findings=[],
                verdict="unknown",  # type: ignore[arg-type]
                human_escalation_path="",
            )

    def test_integration_review_artifact_with_findings(self) -> None:
        finding = ReviewFinding(
            category="integration",
            severity="warning",
            description="API contract mismatch",
            confidence=0.9,
            reviewer_role="integration",
        )
        artifact = IntegrationReviewArtifact(
            run_id="run-xyz",
            reviewer_id="agent-002",
            reviewer_role="integration",
            timestamp="2026-03-03T06:00:00Z",
            git_diff_hash="deadbeef",
            shards_reviewed=["shard-03"],
            checks_performed=["api_contract_mismatch"],
            findings=[finding],
            verdict="warn",
            human_escalation_path="Escalate to team lead via GitHub PR comment",
        )
        assert len(artifact.findings) == 1
        assert artifact.findings[0].severity == "warning"
        assert artifact.findings[0].confidence == 0.9

    def test_integration_review_artifact_is_frozen(self) -> None:
        artifact = IntegrationReviewArtifact(
            run_id="r",
            reviewer_id="",
            reviewer_role="integration",
            timestamp="2026-03-03T00:00:00Z",
            git_diff_hash="",
            shards_reviewed=[],
            checks_performed=[],
            findings=[],
            verdict="pass",
            human_escalation_path="",
        )
        with pytest.raises(ValidationError):
            artifact.verdict = "block"  # type: ignore[misc]
