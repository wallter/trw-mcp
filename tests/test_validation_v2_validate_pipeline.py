"""Tests for the full validation v2 pipeline."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.models.config import TRWConfig, reload_config
from trw_mcp.models.requirements import QualityTier, ValidationResult, ValidationResultV2
from trw_mcp.state.validation import validate_prd_quality, validate_prd_quality_v2

from ._validation_v2_support import (
    _FILLED_PRD,
    _PARTIAL_PRD,
    _SKELETON_PRD,
    _build_integrity_prd,
)


class TestValidatePrdQualityV2:
    """Test the full validate_prd_quality_v2 orchestrator."""

    def test_skeleton_prd_detection(self) -> None:
        result = validate_prd_quality_v2(_SKELETON_PRD)
        assert result.quality_tier in (QualityTier.SKELETON, QualityTier.DRAFT)
        assert result.total_score < 60.0
        assert result.grade in {"F", "D"}

    def test_filled_prd_scores_above_draft(self) -> None:
        result = validate_prd_quality_v2(_FILLED_PRD)
        assert result.total_score > 30.0
        assert result.quality_tier in (QualityTier.DRAFT, QualityTier.REVIEW, QualityTier.APPROVED)

    def test_partial_prd_scores_draft_tier(self) -> None:
        result = validate_prd_quality_v2(_PARTIAL_PRD)
        assert result.quality_tier in (QualityTier.SKELETON, QualityTier.DRAFT)

    def test_v2_populates_v1_fields(self) -> None:
        result = validate_prd_quality_v2(_FILLED_PRD)
        assert hasattr(result, "valid")
        assert hasattr(result, "failures")
        assert hasattr(result, "completeness_score")

    def test_v2_has_4_dimensions(self) -> None:
        result = validate_prd_quality_v2(_FILLED_PRD)
        assert len(result.dimensions) == 4
        dim_names = {d.name for d in result.dimensions}
        assert dim_names == {
            "content_density",
            "structural_completeness",
            "implementation_readiness",
            "traceability",
        }

    def test_v2_no_stub_dimensions(self) -> None:
        result = validate_prd_quality_v2(_FILLED_PRD)
        stub_names = {"smell_score", "readability", "ears_coverage"}
        for dim in result.dimensions:
            assert dim.name not in stub_names, f"Stub dimension found: {dim.name}"
            assert dim.max_score > 0.0, f"Dimension {dim.name} has max_score=0.0"

    def test_v2_retains_deprecated_fields(self) -> None:
        result = validate_prd_quality_v2(_FILLED_PRD)
        assert hasattr(result, "completeness_score")
        assert hasattr(result, "consistency_score")
        assert hasattr(result, "smell_findings")
        assert hasattr(result, "readability")
        assert hasattr(result, "ears_classifications")

    def test_v2_total_score_range(self) -> None:
        result = validate_prd_quality_v2(_FILLED_PRD)
        assert 0.0 <= result.total_score <= 100.0

    def test_v2_section_scores_populated(self) -> None:
        result = validate_prd_quality_v2(_FILLED_PRD)
        assert len(result.section_scores) >= 10

    def test_v2_improvement_suggestions(self) -> None:
        result = validate_prd_quality_v2(_SKELETON_PRD)
        assert len(result.improvement_suggestions) >= 1

    def test_v2_integrity_rejects_unsupported_category(self, tmp_path: Path) -> None:
        repo_file = tmp_path / "src" / "existing.py"
        repo_file.parent.mkdir(parents=True)
        repo_file.write_text("value = 1\n", encoding="utf-8")

        result = validate_prd_quality_v2(
            _build_integrity_prd(
                prd_id="PRD-OPENCODE-001",
                title="Unsupported category fixture",
                category="OPENCODE",
                path_ref="src/existing.py",
            ),
            project_root=str(tmp_path),
        )

        assert result.valid is False
        assert any(f.field == "category" and "Unsupported PRD category" in f.message for f in result.failures)

    def test_v2_integrity_accepts_eval_category(self, tmp_path: Path) -> None:
        repo_file = tmp_path / "src" / "existing.py"
        repo_file.parent.mkdir(parents=True)
        repo_file.write_text("value = 1\n", encoding="utf-8")

        reload_config(TRWConfig(extra_prd_categories=["EVAL"]))
        try:
            result = validate_prd_quality_v2(
                _build_integrity_prd(
                    prd_id="PRD-EVAL-005",
                    title="Eval category fixture",
                    category="EVAL",
                    path_ref="src/existing.py",
                ),
                project_root=str(tmp_path),
            )
        finally:
            reload_config(None)

        assert not any(f.field == "category" for f in result.failures)

    def test_v2_integrity_rejects_missing_repo_reference(self, tmp_path: Path) -> None:
        result = validate_prd_quality_v2(
            _build_integrity_prd(
                prd_id="PRD-QUAL-998",
                title="Missing path fixture",
                category="QUAL",
                path_ref="src/missing.py",
            ),
            project_root=str(tmp_path),
        )

        assert result.valid is False
        assert any("Referenced repo path does not exist" in f.message for f in result.failures)

    def test_v2_integrity_warns_on_probable_duplicate(self, tmp_path: Path) -> None:
        shared_file = tmp_path / "src" / "shared.py"
        shared_file.parent.mkdir(parents=True)
        shared_file.write_text("value = 1\n", encoding="utf-8")

        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)
        existing_prd = _build_integrity_prd(
            prd_id="PRD-QUAL-042",
            title="Client surface contract hardening",
            category="QUAL",
            path_ref="src/shared.py",
        )
        (prds_dir / "PRD-QUAL-042.md").write_text(existing_prd, encoding="utf-8")

        result = validate_prd_quality_v2(
            _build_integrity_prd(
                prd_id="PRD-QUAL-060",
                title="Client surface contract hardening plan",
                category="QUAL",
                path_ref="src/shared.py",
            ),
            project_root=str(tmp_path),
        )

        assert any("PRD-QUAL-042" in warning for warning in result.integrity_warnings)

    def test_backward_compat_v1_unchanged(self) -> None:
        v1 = validate_prd_quality(
            {"id": "X", "title": "Y", "version": "1.0", "status": "draft", "priority": "P1"},
            ["Problem Statement"],
        )
        assert isinstance(v1, ValidationResult)
        assert not isinstance(v1, ValidationResultV2)

    def test_config_density_weight_override(self) -> None:
        config = TRWConfig(validation_density_weight=50.0, risk_scaling_enabled=False)
        result = validate_prd_quality_v2(_FILLED_PRD, config=config)
        density_dim = next(d for d in result.dimensions if d.name == "content_density")
        assert density_dim.max_score == 50.0

    def test_config_threshold_override(self) -> None:
        config = TRWConfig(validation_skeleton_threshold=80.0, risk_scaling_enabled=False)
        result = validate_prd_quality_v2(_PARTIAL_PRD, config=config)
        assert result.quality_tier == QualityTier.SKELETON

    def test_content_docs_profile_scores_static_content_without_runtime_switches(self, tmp_path: Path) -> None:
        for path in (
            tmp_path / "platform/public/llms.txt",
            tmp_path / "platform/src/app/(marketing)/page.tsx",
            tmp_path / "platform/src/app/(marketing)/homepage/data.ts",
            tmp_path / "platform/public/llms.test.ts",
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n", encoding="utf-8")

        prd = """---
prd:
  id: PRD-QUAL-999
  title: Content docs profile fixture
  version: '1.0'
  status: draft
  priority: P2
  category: QUAL
  validation_profile: content_docs
  confidence:
    implementation_feasibility: 0.9
    requirement_clarity: 0.9
    estimate_confidence: 0.8
  traceability:
    implements: [PRD-QUAL-081]
    depends_on: [PRD-CORE-080]
    enables: [PRD-QUAL-083]
---
# PRD-QUAL-999: Content docs profile fixture
## 1. Problem Statement
Static LLM-facing copy needs content validation for AI agents without runtime switch matrices.
## 2. Goals & Non-Goals
Goal: verify source parity. Non-goal: runtime behavior.
## 3. User Stories
### US-001
As a maintainer I want static content checks.
## 4. Functional Requirements
### FR01: llms copy
When platform/public/llms.txt changes, then platform/src/app/(marketing)/page.tsx mirrors the install phrase.
**Assertions**:
- `grep_present: "Install TRW Framework" in "platform/public/llms.txt"`
### FR02: homepage data
When platform/src/app/(marketing)/homepage/data.ts changes, then platform/public/llms.txt stays in parity.
**Assertions**:
- `grep_present: "trw_session_start" in "platform/src/app/(marketing)/homepage/data.ts"`
## 5. Non-Functional Requirements
NFR01: No visible layout shift.
## 6. Technical Approach
Update platform/public/llms.txt and platform/src/app/(marketing)/page.tsx from platform/src/app/(marketing)/homepage/data.ts.
## 7. Test Strategy
Unit Tests: platform/public/llms.test.ts checks parity.
Verification: npm run test and pytest tests/test_validation_v2_validate_pipeline.py -q.
## 8. Rollout Plan
Deploy static content. Rollback by reverting the content commit.
## 9. Success Metrics
Both public surfaces contain the same install phrase.
## 10. Dependencies & Risks
Risk: hidden content drift.
## 11. Open Questions
None.
## 12. Traceability Matrix
| Requirement | Implementation | Tests |
|-------------|----------------|-------|
| FR01 | `platform/public/llms.txt`, `platform/src/app/(marketing)/page.tsx` | `platform/public/llms.test.ts` |
| FR02 | `platform/src/app/(marketing)/homepage/data.ts` | `platform/public/llms.test.ts` |
"""

        result = validate_prd_quality_v2(prd, project_root=str(tmp_path))
        readiness = next(d for d in result.dimensions if d.name == "implementation_readiness")
        traceability = next(d for d in result.dimensions if d.name == "traceability")

        assert readiness.details["validation_profile"] == "content_docs"
        assert readiness.score >= 20.0
        assert traceability.details["validation_profile"] == "content_docs"
        assert "ai_operational_evidence_detected" not in traceability.details
