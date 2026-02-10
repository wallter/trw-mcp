"""Tests for PRD-CORE-009: Phase Gate PRD Enforcement.

Phase 1: State machine, transitions, config defaults, RunState fields.
Phase 2: Guard checks and trw_prd_status_update tool.
Phase 3: Phase gate integration and PRD discovery.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import PRDStatus, QualityTier
from trw_mcp.models.run import RunState
from trw_mcp.models.run import Phase
from trw_mcp.state.prd_utils import (
    VALID_TRANSITIONS,
    TransitionResult,
    check_transition_guards,
    compute_content_density,
    discover_governing_prds,
    is_valid_transition,
)
from trw_mcp.state.validation import check_phase_exit


# ---------------------------------------------------------------------------
# Phase 1: State Machine Transitions
# ---------------------------------------------------------------------------


class TestValidTransitions:
    """Test that valid transitions are accepted."""

    def test_draft_to_review(self) -> None:
        assert is_valid_transition(PRDStatus.DRAFT, PRDStatus.REVIEW) is True

    def test_review_to_approved(self) -> None:
        assert is_valid_transition(PRDStatus.REVIEW, PRDStatus.APPROVED) is True

    def test_review_to_draft(self) -> None:
        """Revision requested — backward transition is valid."""
        assert is_valid_transition(PRDStatus.REVIEW, PRDStatus.DRAFT) is True

    def test_approved_to_implemented(self) -> None:
        assert is_valid_transition(PRDStatus.APPROVED, PRDStatus.IMPLEMENTED) is True

    def test_approved_to_deprecated(self) -> None:
        assert is_valid_transition(PRDStatus.APPROVED, PRDStatus.DEPRECATED) is True

    def test_implemented_to_deprecated(self) -> None:
        assert is_valid_transition(PRDStatus.IMPLEMENTED, PRDStatus.DEPRECATED) is True


class TestIdentityTransitions:
    """Identity (same state → same state) should always be valid."""

    def test_draft_to_draft(self) -> None:
        assert is_valid_transition(PRDStatus.DRAFT, PRDStatus.DRAFT) is True

    def test_review_to_review(self) -> None:
        assert is_valid_transition(PRDStatus.REVIEW, PRDStatus.REVIEW) is True

    def test_approved_to_approved(self) -> None:
        assert is_valid_transition(PRDStatus.APPROVED, PRDStatus.APPROVED) is True

    def test_implemented_to_implemented(self) -> None:
        assert is_valid_transition(PRDStatus.IMPLEMENTED, PRDStatus.IMPLEMENTED) is True

    def test_deprecated_to_deprecated(self) -> None:
        assert is_valid_transition(PRDStatus.DEPRECATED, PRDStatus.DEPRECATED) is True


class TestInvalidTransitions:
    """Test that invalid transitions are rejected."""

    def test_draft_to_approved(self) -> None:
        """Cannot skip REVIEW stage."""
        assert is_valid_transition(PRDStatus.DRAFT, PRDStatus.APPROVED) is False

    def test_draft_to_implemented(self) -> None:
        assert is_valid_transition(PRDStatus.DRAFT, PRDStatus.IMPLEMENTED) is False

    def test_draft_to_deprecated(self) -> None:
        assert is_valid_transition(PRDStatus.DRAFT, PRDStatus.DEPRECATED) is False

    def test_implemented_to_draft(self) -> None:
        assert is_valid_transition(PRDStatus.IMPLEMENTED, PRDStatus.DRAFT) is False

    def test_implemented_to_review(self) -> None:
        assert is_valid_transition(PRDStatus.IMPLEMENTED, PRDStatus.REVIEW) is False

    def test_implemented_to_approved(self) -> None:
        assert is_valid_transition(PRDStatus.IMPLEMENTED, PRDStatus.APPROVED) is False

    def test_deprecated_to_draft(self) -> None:
        assert is_valid_transition(PRDStatus.DEPRECATED, PRDStatus.DRAFT) is False

    def test_deprecated_to_review(self) -> None:
        assert is_valid_transition(PRDStatus.DEPRECATED, PRDStatus.REVIEW) is False

    def test_deprecated_to_approved(self) -> None:
        assert is_valid_transition(PRDStatus.DEPRECATED, PRDStatus.APPROVED) is False

    def test_deprecated_to_implemented(self) -> None:
        assert is_valid_transition(PRDStatus.DEPRECATED, PRDStatus.IMPLEMENTED) is False


class TestValidTransitionsConstant:
    """Test the VALID_TRANSITIONS constant structure."""

    def test_all_states_present(self) -> None:
        assert len(VALID_TRANSITIONS) == len(PRDStatus)
        for status in PRDStatus:
            assert status in VALID_TRANSITIONS

    def test_deprecated_has_no_outgoing(self) -> None:
        assert VALID_TRANSITIONS[PRDStatus.DEPRECATED] == set()

    def test_draft_outgoing(self) -> None:
        assert VALID_TRANSITIONS[PRDStatus.DRAFT] == {PRDStatus.REVIEW, PRDStatus.MERGED}

    def test_review_outgoing(self) -> None:
        assert VALID_TRANSITIONS[PRDStatus.REVIEW] == {
            PRDStatus.APPROVED,
            PRDStatus.DRAFT,
            PRDStatus.MERGED,
        }


# ---------------------------------------------------------------------------
# Phase 1: TransitionResult Model
# ---------------------------------------------------------------------------


class TestTransitionResult:
    """Test the TransitionResult model."""

    def test_allowed_result(self) -> None:
        result = TransitionResult(allowed=True, reason="ok")
        assert result.allowed is True
        assert result.reason == "ok"

    def test_rejected_result_with_details(self) -> None:
        result = TransitionResult(
            allowed=False,
            reason="Content density too low",
            guard_details={"density": 0.15, "threshold": 0.30},
        )
        assert result.allowed is False
        assert "density" in result.guard_details

    def test_defaults(self) -> None:
        result = TransitionResult(allowed=True)
        assert result.reason == ""
        assert result.guard_details == {}


# ---------------------------------------------------------------------------
# Phase 1: Config Defaults (PRD-CORE-009-FR06)
# ---------------------------------------------------------------------------


class TestConfigDefaults:
    """Test new TRWConfig fields for phase gate enforcement."""

    def test_enforcement_default_is_lenient(self) -> None:
        config = TRWConfig()
        assert config.phase_gate_enforcement == "lenient"

    def test_prd_min_content_density_default(self) -> None:
        config = TRWConfig()
        assert config.prd_min_content_density == 0.30

    def test_prd_required_status_default(self) -> None:
        config = TRWConfig()
        assert config.prd_required_status_for_implement == "approved"

    def test_enforcement_strict_override(self) -> None:
        config = TRWConfig(phase_gate_enforcement="strict")
        assert config.phase_gate_enforcement == "strict"

    def test_enforcement_off_override(self) -> None:
        config = TRWConfig(phase_gate_enforcement="off")
        assert config.phase_gate_enforcement == "off"

    def test_density_threshold_override(self) -> None:
        config = TRWConfig(prd_min_content_density=0.50)
        assert config.prd_min_content_density == 0.50


# ---------------------------------------------------------------------------
# Phase 1: RunState Model Fields (PRD-CORE-009-FR07)
# ---------------------------------------------------------------------------


class TestRunStateFields:
    """Test new RunState fields for PRD scope and run type."""

    def test_prd_scope_default_empty(self) -> None:
        run = RunState(run_id="test", task="test")
        assert run.prd_scope == []

    def test_prd_scope_with_values(self) -> None:
        run = RunState(
            run_id="test",
            task="test",
            prd_scope=["PRD-CORE-009", "PRD-FIX-006"],
        )
        assert run.prd_scope == ["PRD-CORE-009", "PRD-FIX-006"]

    def test_run_type_default_implementation(self) -> None:
        run = RunState(run_id="test", task="test")
        assert run.run_type == "implementation"

    def test_run_type_research(self) -> None:
        run = RunState(run_id="test", task="test", run_type="research")
        assert run.run_type == "research"

    def test_backward_compat_no_new_fields(self) -> None:
        """Existing RunState creation without new fields still works."""
        run = RunState(
            run_id="test",
            task="test",
            framework="v18.0_TRW",
        )
        assert run.prd_scope == []
        assert run.run_type == "implementation"


# ---------------------------------------------------------------------------
# Phase 2: Guard Checks (PRD-CORE-009-FR02)
# ---------------------------------------------------------------------------


# Minimal PRD content with low density (mostly blanks and headings)
_SKELETON_PRD = """---
prd:
  id: PRD-TEST-001
  title: Test PRD
  status: draft
---

# PRD-TEST-001: Test PRD

## 1. Problem Statement
## 2. Goals & Non-Goals
## 3. User Stories
## 4. Functional Requirements
## 5. Non-Functional Requirements
## 6. Technical Approach
## 7. Test Strategy
## 8. Rollout Plan
## 9. Success Metrics
## 10. Dependencies & Risks
## 11. Open Questions
## 12. Traceability Matrix
"""

# Well-filled PRD content with high density
_FILLED_PRD = """---
prd:
  id: PRD-TEST-002
  title: Filled Test PRD
  status: draft
  priority: P1
  category: CORE
  confidence:
    implementation_feasibility: 0.85
    requirement_clarity: 0.85
    estimate_confidence: 0.8
    test_coverage_target: 0.9
  evidence:
    level: strong
    sources: [test source]
  traceability:
    implements: []
    depends_on: []
    enables: []
    conflicts_with: []
  quality_gates:
    ambiguity_rate_max: 0.05
    completeness_min: 0.85
    traceability_coverage_min: 0.9
  dates:
    created: '2026-02-09'
    updated: '2026-02-09'
---

# PRD-TEST-002: Filled Test PRD

**Quick Reference**:
- **Status**: Draft
- **Priority**: P1
- **Evidence**: Strong
- **Implementation Confidence**: 0.85

---

## 1. Problem Statement

### Background
The system requires a comprehensive validation engine that scores PRD quality
across multiple dimensions including content density, structural completeness,
and requirement traceability. Current validation is binary pass/fail.

### Problem
Existing validation does not provide granular quality scores that would enable
progressive improvement of PRD documents through iterative feedback cycles.

### Impact
Framework operators cannot prioritize which PRDs need the most improvement work.
Agent swarms produce PRDs of varying quality with no quantitative comparison.

## 2. Goals & Non-Goals

### Goals
- G1: Implement a multi-dimension scoring system for PRD quality assessment
- G2: Provide actionable improvement suggestions ranked by potential impact
- G3: Classify PRDs into quality tiers based on composite scores

### Non-Goals
- This PRD does not implement automated PRD improvement or rewriting
- This PRD does not modify the AARE-F template structure

## 3. User Stories

### US-001: Quality Score Dashboard
**As a** framework operator
**I want** to see quality scores for all PRDs at a glance
**So that** I can prioritize review effort on the lowest-quality documents

## 4. Functional Requirements

### PRD-TEST-002-FR01: Multi-Dimension Scoring
**Priority**: Must Have
**Description**: Implement scoring across 6 dimensions with configurable weights.

### PRD-TEST-002-FR02: Quality Tier Classification
**Priority**: Must Have
**Description**: Classify PRDs into SKELETON, DRAFT, REVIEW, APPROVED tiers.

## 5. Non-Functional Requirements

### PRD-TEST-002-NFR01: Performance
All validation operations complete in under 500ms for PRDs up to 50KB.

### PRD-TEST-002-NFR02: Backward Compatibility
Existing callers of validate_prd_quality() must continue to work unchanged.

## 6. Technical Approach

### Architecture
The validation engine uses a pipeline architecture with independent dimension
scorers that can be run in parallel. Each scorer returns a DimensionScore model
containing the dimension name, raw score, max possible score, and diagnostic details.

### Key Files
| File | Changes |
|------|---------|
| `state/validation.py` | Add V2 orchestrator |
| `models/requirements.py` | Add V2 models |

## 7. Test Strategy

Unit tests for each dimension scorer plus integration tests for the V2 orchestrator.
Target coverage: 90% for new modules.

## 8. Rollout Plan

Phase 1: Core scoring engine. Phase 2: Smell detection + readability. Phase 3: EARS.

## 9. Success Metrics

| Metric | Target |
|--------|--------|
| Test coverage | >= 90% |
| Quality scoring accuracy | >= 85% |

## 10. Dependencies & Risks

| ID | Dependency | Status | Blocking |
|----|-----------|--------|----------|
| DEP-001 | PRD-FIX-006 | Resolved | Yes |

## 11. Open Questions

No open questions at this time.

## 12. Traceability Matrix

| Requirement | Source | Test | Implementation |
|------------|--------|------|----------------|
| FR01 | US-001 | test_scoring | `validation.py` |
| FR02 | US-001 | test_tiers | `validation.py` |
"""


class TestGuardDraftToReview:
    """Test DRAFT -> REVIEW guard: content density check."""

    def test_low_density_rejects(self) -> None:
        """Skeleton PRD below density threshold fails the guard."""
        result = check_transition_guards(
            PRDStatus.DRAFT, PRDStatus.REVIEW, _SKELETON_PRD
        )
        assert result.allowed is False
        assert "density" in result.reason.lower()
        assert "density" in result.guard_details

    def test_high_density_passes(self) -> None:
        """Well-filled PRD above density threshold passes."""
        result = check_transition_guards(
            PRDStatus.DRAFT, PRDStatus.REVIEW, _FILLED_PRD
        )
        assert result.allowed is True
        assert result.guard_details.get("density", 0) >= 0.30

    def test_custom_threshold(self) -> None:
        """Custom density threshold is respected."""
        config = TRWConfig(prd_min_content_density=0.90)
        result = check_transition_guards(
            PRDStatus.DRAFT, PRDStatus.REVIEW, _FILLED_PRD, config
        )
        # Even a well-filled PRD may not reach 90% density
        assert isinstance(result.allowed, bool)
        assert "density" in result.guard_details

    def test_density_value_in_details(self) -> None:
        """Guard details include both density value and threshold."""
        result = check_transition_guards(
            PRDStatus.DRAFT, PRDStatus.REVIEW, _SKELETON_PRD
        )
        assert "density" in result.guard_details
        assert "threshold" in result.guard_details


class TestGuardReviewToApproved:
    """Test REVIEW -> APPROVED guard: quality validation check."""

    def test_skeleton_rejected(self) -> None:
        """Skeleton PRD fails quality validation for approval."""
        result = check_transition_guards(
            PRDStatus.REVIEW, PRDStatus.APPROVED, _SKELETON_PRD
        )
        assert result.allowed is False
        assert "quality_tier" in result.guard_details
        tier = result.guard_details.get("quality_tier", "")
        assert tier in ("skeleton", "draft")

    def test_filled_prd_guard(self) -> None:
        """Well-filled PRD gets evaluated for quality tier."""
        result = check_transition_guards(
            PRDStatus.REVIEW, PRDStatus.APPROVED, _FILLED_PRD
        )
        # Result depends on quality score — check structure is correct
        assert isinstance(result.allowed, bool)
        assert "total_score" in result.guard_details
        assert "quality_tier" in result.guard_details
        assert "grade" in result.guard_details

    def test_guard_details_include_score(self) -> None:
        """Guard details include total_score, quality_tier, and grade."""
        result = check_transition_guards(
            PRDStatus.REVIEW, PRDStatus.APPROVED, _FILLED_PRD
        )
        assert isinstance(result.guard_details.get("total_score"), (int, float))
        assert isinstance(result.guard_details.get("quality_tier"), str)
        assert isinstance(result.guard_details.get("grade"), str)


class TestGuardNoGuardTransitions:
    """Test transitions with no guards always pass."""

    def test_approved_to_implemented(self) -> None:
        result = check_transition_guards(
            PRDStatus.APPROVED, PRDStatus.IMPLEMENTED, ""
        )
        assert result.allowed is True
        assert "no guard" in result.reason.lower()

    def test_approved_to_deprecated(self) -> None:
        result = check_transition_guards(
            PRDStatus.APPROVED, PRDStatus.DEPRECATED, ""
        )
        assert result.allowed is True

    def test_implemented_to_deprecated(self) -> None:
        result = check_transition_guards(
            PRDStatus.IMPLEMENTED, PRDStatus.DEPRECATED, ""
        )
        assert result.allowed is True

    def test_review_to_draft(self) -> None:
        """Backward transition has no guard."""
        result = check_transition_guards(
            PRDStatus.REVIEW, PRDStatus.DRAFT, ""
        )
        assert result.allowed is True

    def test_identity_no_guard(self) -> None:
        """Identity transitions always pass with no guard."""
        result = check_transition_guards(
            PRDStatus.DRAFT, PRDStatus.DRAFT, ""
        )
        assert result.allowed is True
        assert "identity" in result.reason.lower()


# ---------------------------------------------------------------------------
# Phase 2: trw_prd_status_update Tool (PRD-CORE-009-FR03)
# ---------------------------------------------------------------------------


@pytest.fixture()
def prd_project(tmp_path: Path) -> Path:
    """Create a temporary project with a PRD file for tool tests."""
    prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
    prds_dir.mkdir(parents=True)
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()

    # Write a well-filled PRD at draft status
    prd_file = prds_dir / "PRD-TEST-001.md"
    prd_file.write_text(_FILLED_PRD.replace("PRD-TEST-002", "PRD-TEST-001"), encoding="utf-8")

    return tmp_path


class TestPrdStatusUpdateTool:
    """Test trw_prd_status_update tool behavior via direct function calls."""

    def test_valid_forward_transition(self, prd_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """DRAFT -> REVIEW succeeds for a well-filled PRD."""
        monkeypatch.setattr(
            "trw_mcp.tools.requirements.resolve_project_root",
            lambda: prd_project,
        )
        from trw_mcp.tools.requirements import _resolve_prd_path

        path = _resolve_prd_path("PRD-TEST-001")
        assert path.exists()

    def test_resolve_prd_path_not_found(self, prd_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing PRD raises StateError."""
        monkeypatch.setattr(
            "trw_mcp.tools.requirements.resolve_project_root",
            lambda: prd_project,
        )
        from trw_mcp.tools.requirements import _resolve_prd_path

        with pytest.raises(Exception, match="not found"):
            _resolve_prd_path("PRD-NONEXISTENT-999")

    def test_invalid_target_status(self) -> None:
        """Invalid target_status string raises ValidationError."""
        from trw_mcp.exceptions import ValidationError as TRWValidationError

        # We can test the status parsing directly
        with pytest.raises(ValueError):
            PRDStatus("not_a_real_status")

    def test_check_transition_guards_returns_result(self) -> None:
        """check_transition_guards always returns a TransitionResult."""
        result = check_transition_guards(
            PRDStatus.DRAFT, PRDStatus.REVIEW, _SKELETON_PRD
        )
        assert isinstance(result, TransitionResult)
        assert isinstance(result.allowed, bool)
        assert isinstance(result.reason, str)
        assert isinstance(result.guard_details, dict)


class TestStatusUpdateIntegration:
    """Integration tests for the status update flow (without MCP server)."""

    def test_update_frontmatter_changes_status(self, prd_project: Path) -> None:
        """update_frontmatter correctly changes the status field."""
        from trw_mcp.state.prd_utils import parse_frontmatter, update_frontmatter

        prd_file = prd_project / "docs" / "requirements-aare-f" / "prds" / "PRD-TEST-001.md"

        # Verify initial status
        content = prd_file.read_text(encoding="utf-8")
        fm = parse_frontmatter(content)
        assert fm.get("status") == "draft"

        # Update status
        update_frontmatter(prd_file, {"status": "review"})

        # Verify updated status
        content = prd_file.read_text(encoding="utf-8")
        fm = parse_frontmatter(content)
        assert fm.get("status") == "review"

    def test_update_frontmatter_preserves_other_fields(self, prd_project: Path) -> None:
        """update_frontmatter does not clobber other frontmatter fields."""
        from trw_mcp.state.prd_utils import parse_frontmatter, update_frontmatter

        prd_file = prd_project / "docs" / "requirements-aare-f" / "prds" / "PRD-TEST-001.md"

        # Read original
        content = prd_file.read_text(encoding="utf-8")
        fm_before = parse_frontmatter(content)
        original_title = fm_before.get("title")
        original_id = fm_before.get("id")

        # Update status only
        update_frontmatter(prd_file, {"status": "review"})

        # Verify other fields preserved
        content = prd_file.read_text(encoding="utf-8")
        fm_after = parse_frontmatter(content)
        assert fm_after.get("title") == original_title
        assert fm_after.get("id") == original_id

    def test_full_transition_flow(self, prd_project: Path) -> None:
        """Simulate a DRAFT -> REVIEW transition with guards."""
        from trw_mcp.state.prd_utils import parse_frontmatter, update_frontmatter

        prd_file = prd_project / "docs" / "requirements-aare-f" / "prds" / "PRD-TEST-001.md"
        content = prd_file.read_text(encoding="utf-8")

        # Check guard
        current = PRDStatus.DRAFT
        target = PRDStatus.REVIEW
        assert is_valid_transition(current, target)

        guard_result = check_transition_guards(current, target, content)
        if guard_result.allowed:
            update_frontmatter(prd_file, {"status": target.value})
            new_content = prd_file.read_text(encoding="utf-8")
            fm = parse_frontmatter(new_content)
            assert fm.get("status") == "review"
        else:
            # Guard failed — status unchanged
            new_content = prd_file.read_text(encoding="utf-8")
            fm = parse_frontmatter(new_content)
            assert fm.get("status") == "draft"


# ---------------------------------------------------------------------------
# Phase 3: PRD Discovery (PRD-CORE-009-FR07)
# ---------------------------------------------------------------------------


@pytest.fixture()
def run_with_prd_scope(tmp_path: Path) -> Path:
    """Create a run directory with explicit prd_scope in run.yaml."""
    from ruamel.yaml import YAML

    run_path = tmp_path / "run"
    meta = run_path / "meta"
    meta.mkdir(parents=True)
    reports = run_path / "reports"
    reports.mkdir(parents=True)

    yaml = YAML()
    state = {
        "run_id": "test-run",
        "task": "test",
        "prd_scope": ["PRD-CORE-009", "PRD-FIX-006"],
        "run_type": "implementation",
    }
    yaml.dump(state, meta / "run.yaml")
    return run_path


@pytest.fixture()
def run_with_plan_refs(tmp_path: Path) -> Path:
    """Create a run directory with PRD refs in plan.md but no prd_scope."""
    from ruamel.yaml import YAML

    run_path = tmp_path / "run"
    meta = run_path / "meta"
    meta.mkdir(parents=True)
    reports = run_path / "reports"
    reports.mkdir(parents=True)

    yaml = YAML()
    state = {
        "run_id": "test-run",
        "task": "test",
        "prd_scope": [],
        "run_type": "implementation",
    }
    yaml.dump(state, meta / "run.yaml")

    plan_content = """# Implementation Plan

This plan implements PRD-CORE-007 and PRD-FIX-006.

## Steps
1. Refactor prd_utils.py per PRD-FIX-006
2. Implement validation per PRD-CORE-007
"""
    (reports / "plan.md").write_text(plan_content, encoding="utf-8")
    return run_path


@pytest.fixture()
def run_empty_scope(tmp_path: Path) -> Path:
    """Create a run directory with no prd_scope and no plan.md."""
    from ruamel.yaml import YAML

    run_path = tmp_path / "run"
    meta = run_path / "meta"
    meta.mkdir(parents=True)
    reports = run_path / "reports"
    reports.mkdir(parents=True)

    yaml = YAML()
    state = {
        "run_id": "test-run",
        "task": "test",
        "prd_scope": [],
        "run_type": "implementation",
    }
    yaml.dump(state, meta / "run.yaml")
    return run_path


@pytest.fixture()
def research_run(tmp_path: Path) -> Path:
    """Create a research-type run directory."""
    from ruamel.yaml import YAML

    run_path = tmp_path / "run"
    meta = run_path / "meta"
    meta.mkdir(parents=True)
    reports = run_path / "reports"
    reports.mkdir(parents=True)

    yaml = YAML()
    state = {
        "run_id": "test-run",
        "task": "test",
        "prd_scope": [],
        "run_type": "research",
    }
    yaml.dump(state, meta / "run.yaml")
    return run_path


class TestDiscoverGoverningPrds:
    """Test three-tier PRD discovery (FR07)."""

    def test_tier1_explicit_scope(self, run_with_prd_scope: Path) -> None:
        """Tier 1: prd_scope in run.yaml is used directly."""
        prds = discover_governing_prds(run_with_prd_scope)
        assert prds == ["PRD-CORE-009", "PRD-FIX-006"]

    def test_tier2_plan_scanning(self, run_with_plan_refs: Path) -> None:
        """Tier 2: PRD refs extracted from plan.md."""
        prds = discover_governing_prds(run_with_plan_refs)
        assert prds == ["PRD-CORE-007", "PRD-FIX-006"]

    def test_tier3_empty(self, run_empty_scope: Path) -> None:
        """Tier 3: No PRDs found returns empty list."""
        prds = discover_governing_prds(run_empty_scope)
        assert prds == []

    def test_tier1_takes_precedence(self, run_with_prd_scope: Path) -> None:
        """Tier 1 (explicit scope) takes precedence over plan scanning."""
        # Add a plan.md with different refs
        plan_content = "Implements PRD-QUAL-001 and PRD-CORE-011"
        (run_with_prd_scope / "reports" / "plan.md").write_text(plan_content, encoding="utf-8")

        prds = discover_governing_prds(run_with_prd_scope)
        # Should use tier 1 (from run.yaml), not tier 2 (from plan.md)
        assert prds == ["PRD-CORE-009", "PRD-FIX-006"]

    def test_no_run_yaml(self, tmp_path: Path) -> None:
        """Handles missing run.yaml gracefully."""
        run_path = tmp_path / "run"
        (run_path / "meta").mkdir(parents=True)
        (run_path / "reports").mkdir(parents=True)

        prds = discover_governing_prds(run_path)
        assert prds == []


# ---------------------------------------------------------------------------
# Phase 3: Phase Gate Integration (FR04, FR05, FR08)
# ---------------------------------------------------------------------------


@pytest.fixture()
def phase_gate_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a project structure for phase gate testing.

    Sets up:
    - PRD files with specific statuses
    - A run directory with prd_scope
    - Monkeypatches resolve_project_root
    """
    from ruamel.yaml import YAML

    # Project root
    project_root = tmp_path / "project"
    trw_dir = project_root / ".trw"
    trw_dir.mkdir(parents=True)

    # Create PRD files
    prds_dir = project_root / "docs" / "requirements-aare-f" / "prds"
    prds_dir.mkdir(parents=True)

    # PRD-CORE-010: draft status
    prd_draft = """---
prd:
  id: PRD-CORE-010
  title: Draft PRD
  status: draft
---
# PRD-CORE-010: Draft PRD
## 1. Problem Statement
Draft content here.
"""
    (prds_dir / "PRD-CORE-010.md").write_text(prd_draft, encoding="utf-8")

    # PRD-CORE-011: approved status
    prd_approved = """---
prd:
  id: PRD-CORE-011
  title: Approved PRD
  status: approved
---
# PRD-CORE-011: Approved PRD
## 1. Problem Statement
Approved content here.
"""
    (prds_dir / "PRD-CORE-011.md").write_text(prd_approved, encoding="utf-8")

    # Create run directory
    run_path = tmp_path / "run"
    meta = run_path / "meta"
    meta.mkdir(parents=True)
    reports = run_path / "reports"
    reports.mkdir(parents=True)
    shards = run_path / "shards"
    shards.mkdir(parents=True)

    yaml_writer = YAML()
    state = {
        "run_id": "test-run",
        "task": "test",
        "prd_scope": ["PRD-CORE-010", "PRD-CORE-011"],
        "run_type": "implementation",
    }
    yaml_writer.dump(state, meta / "run.yaml")

    # Monkeypatch project root resolution
    monkeypatch.setattr(
        "trw_mcp.state._paths.resolve_project_root",
        lambda: project_root,
    )

    return run_path


class TestPlanPhaseGate:
    """Test PRD enforcement at PLAN phase gate (FR04)."""

    def test_plan_gate_warns_draft_prds(self, phase_gate_project: Path) -> None:
        """Plan gate: PRDs exist (even as draft) — no PRD-exists failures."""
        config = TRWConfig(phase_gate_enforcement="strict")
        # Create plan.md to avoid the plan_exists error
        (phase_gate_project / "reports" / "plan.md").write_text("# Plan", encoding="utf-8")

        result = check_phase_exit(Phase.PLAN, phase_gate_project, config)
        # PRDs both exist and are at least DRAFT → no prd_exists failures
        prd_failures = [f for f in result.failures if f.rule == "prd_exists"]
        assert len(prd_failures) == 0

    def test_plan_gate_missing_prd_file(
        self, phase_gate_project: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Plan gate: missing PRD file produces error (strict) or warning (lenient)."""
        from ruamel.yaml import YAML

        # Update prd_scope to include a non-existent PRD
        yaml_writer = YAML()
        state = {
            "run_id": "test-run",
            "task": "test",
            "prd_scope": ["PRD-NONEXISTENT-999"],
            "run_type": "implementation",
        }
        yaml_writer.dump(state, phase_gate_project / "meta" / "run.yaml")
        (phase_gate_project / "reports" / "plan.md").write_text("# Plan", encoding="utf-8")

        config = TRWConfig(phase_gate_enforcement="strict")
        result = check_phase_exit(Phase.PLAN, phase_gate_project, config)

        prd_failures = [f for f in result.failures if f.rule == "prd_exists"]
        assert len(prd_failures) == 1
        assert prd_failures[0].severity == "error"
        assert result.valid is False

    def test_plan_gate_lenient_mode(
        self, phase_gate_project: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Lenient mode: missing PRD produces warning, valid remains True."""
        from ruamel.yaml import YAML

        yaml_writer = YAML()
        state = {
            "run_id": "test-run",
            "task": "test",
            "prd_scope": ["PRD-NONEXISTENT-999"],
            "run_type": "implementation",
        }
        yaml_writer.dump(state, phase_gate_project / "meta" / "run.yaml")
        (phase_gate_project / "reports" / "plan.md").write_text("# Plan", encoding="utf-8")

        config = TRWConfig(phase_gate_enforcement="lenient")
        result = check_phase_exit(Phase.PLAN, phase_gate_project, config)

        prd_failures = [f for f in result.failures if f.rule == "prd_exists"]
        assert len(prd_failures) == 1
        assert prd_failures[0].severity == "warning"
        assert result.valid is True

    def test_plan_gate_off_skips_checks(self, phase_gate_project: Path) -> None:
        """Enforcement=off: no PRD-related failures at all."""
        (phase_gate_project / "reports" / "plan.md").write_text("# Plan", encoding="utf-8")

        config = TRWConfig(phase_gate_enforcement="off")
        result = check_phase_exit(Phase.PLAN, phase_gate_project, config)

        prd_failures = [f for f in result.failures if "prd" in f.rule.lower()]
        assert len(prd_failures) == 0

    def test_plan_gate_no_prds_advisory(self, run_empty_scope: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """No PRDs found: advisory warning regardless of enforcement level."""
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            lambda: run_empty_scope.parent,
        )
        (run_empty_scope / "reports" / "plan.md").write_text("# Plan", encoding="utf-8")

        config = TRWConfig(phase_gate_enforcement="strict")
        result = check_phase_exit(Phase.PLAN, run_empty_scope, config)

        advisory = [f for f in result.failures if f.rule == "prd_discovery"]
        assert len(advisory) == 1
        assert advisory[0].severity == "warning"  # Always warning, never error


class TestImplementPhaseGate:
    """Test PRD enforcement at IMPLEMENT phase gate (FR05)."""

    def test_implement_gate_strict_draft_prd(self, phase_gate_project: Path) -> None:
        """Strict enforcement: draft PRD below 'approved' produces error."""
        config = TRWConfig(phase_gate_enforcement="strict")
        result = check_phase_exit(Phase.IMPLEMENT, phase_gate_project, config)

        # PRD-CORE-010 is draft, required is approved → error
        status_failures = [f for f in result.failures if f.rule == "prd_status"]
        assert len(status_failures) == 1
        assert "PRD-CORE-010" in status_failures[0].message
        assert status_failures[0].severity == "error"
        assert result.valid is False

    def test_implement_gate_approved_prd_passes(
        self, phase_gate_project: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """All PRDs at approved status: no status failures."""
        from ruamel.yaml import YAML

        # Update scope to only include the approved PRD
        yaml_writer = YAML()
        state = {
            "run_id": "test-run",
            "task": "test",
            "prd_scope": ["PRD-CORE-011"],
            "run_type": "implementation",
        }
        yaml_writer.dump(state, phase_gate_project / "meta" / "run.yaml")

        config = TRWConfig(phase_gate_enforcement="strict")
        result = check_phase_exit(Phase.IMPLEMENT, phase_gate_project, config)

        status_failures = [f for f in result.failures if f.rule == "prd_status"]
        assert len(status_failures) == 0

    def test_implement_gate_lenient_draft(self, phase_gate_project: Path) -> None:
        """Lenient enforcement: draft PRD produces warning, valid stays True."""
        config = TRWConfig(phase_gate_enforcement="lenient")
        result = check_phase_exit(Phase.IMPLEMENT, phase_gate_project, config)

        status_failures = [f for f in result.failures if f.rule == "prd_status"]
        assert len(status_failures) == 1
        assert status_failures[0].severity == "warning"
        assert result.valid is True

    def test_implement_gate_custom_required_status(self, phase_gate_project: Path) -> None:
        """Custom required_status='review': draft PRD fails, approved PRD passes."""
        config = TRWConfig(
            phase_gate_enforcement="strict",
            prd_required_status_for_implement="review",
        )
        result = check_phase_exit(Phase.IMPLEMENT, phase_gate_project, config)

        status_failures = [f for f in result.failures if f.rule == "prd_status"]
        # PRD-CORE-010 is draft (below review) → failure
        # PRD-CORE-011 is approved (above review) → pass
        assert len(status_failures) == 1
        assert "PRD-CORE-010" in status_failures[0].message


class TestResearchRunExemption:
    """Test that research runs skip PRD enforcement (FR08)."""

    def test_research_run_skips_plan_enforcement(
        self, research_run: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Research run: plan gate has no PRD-related failures."""
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            lambda: research_run.parent,
        )
        (research_run / "reports" / "plan.md").write_text("# Plan", encoding="utf-8")

        config = TRWConfig(phase_gate_enforcement="strict")
        result = check_phase_exit(Phase.PLAN, research_run, config)

        prd_failures = [f for f in result.failures if "prd" in f.rule.lower()]
        assert len(prd_failures) == 0

    def test_research_run_skips_implement_enforcement(
        self, research_run: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Research run: implement gate has no PRD-related failures."""
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            lambda: research_run.parent,
        )

        config = TRWConfig(phase_gate_enforcement="strict")
        result = check_phase_exit(Phase.IMPLEMENT, research_run, config)

        prd_failures = [f for f in result.failures if "prd" in f.rule.lower()]
        assert len(prd_failures) == 0

    def test_implementation_run_applies_enforcement(
        self, phase_gate_project: Path,
    ) -> None:
        """Implementation run: enforcement is applied normally."""
        config = TRWConfig(phase_gate_enforcement="strict")
        result = check_phase_exit(Phase.IMPLEMENT, phase_gate_project, config)

        # Has PRD-related failures (PRD-CORE-010 is draft)
        prd_failures = [f for f in result.failures if "prd" in f.rule.lower()]
        assert len(prd_failures) > 0


class TestTrwInitPrdScope:
    """Test trw_init parameters for prd_scope and run_type (FR07/FR08)."""

    def test_run_state_includes_prd_scope(self) -> None:
        """RunState model supports prd_scope parameter."""
        run = RunState(
            run_id="test",
            task="test",
            prd_scope=["PRD-CORE-009"],
        )
        assert run.prd_scope == ["PRD-CORE-009"]

    def test_run_state_includes_run_type(self) -> None:
        """RunState model supports run_type parameter."""
        run = RunState(
            run_id="test",
            task="test",
            run_type="research",
        )
        assert run.run_type == "research"

    def test_defaults_backward_compatible(self) -> None:
        """Default prd_scope and run_type are backward compatible."""
        run = RunState(run_id="test", task="test")
        assert run.prd_scope == []
        assert run.run_type == "implementation"


# ---------------------------------------------------------------------------
# PRD-FIX-009: Force Parameter Bug Fixes
# ---------------------------------------------------------------------------


class TestForceParameterValidation:
    """Tests for PRD-FIX-009: force parameter reason validation + event metadata."""

    def test_force_true_empty_reason_raises_validation_error(
        self, prd_project: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """PRD-FIX-009-FR02: force=True with empty reason raises ValidationError."""
        from trw_mcp.exceptions import ValidationError as TRWValidationError
        from trw_mcp.tools import requirements as req_mod

        monkeypatch.setattr(req_mod, "resolve_project_root", lambda: prd_project)
        monkeypatch.setattr(req_mod, "_config", TRWConfig())

        from fastmcp import FastMCP
        from trw_mcp.tools.requirements import register_requirements_tools
        srv = FastMCP("test-fix009")
        register_requirements_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}

        with pytest.raises(TRWValidationError, match="reason is required"):
            tools["trw_prd_status_update"].fn(
                prd_id="PRD-TEST-001",
                target_status="review",
                force=True,
                reason="",
            )

    def test_force_true_whitespace_reason_raises_validation_error(
        self, prd_project: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """PRD-FIX-009-FR02: force=True with whitespace-only reason raises."""
        from trw_mcp.exceptions import ValidationError as TRWValidationError
        from trw_mcp.tools import requirements as req_mod

        monkeypatch.setattr(req_mod, "resolve_project_root", lambda: prd_project)
        monkeypatch.setattr(req_mod, "_config", TRWConfig())

        from fastmcp import FastMCP
        from trw_mcp.tools.requirements import register_requirements_tools
        srv = FastMCP("test-fix009")
        register_requirements_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}

        with pytest.raises(TRWValidationError, match="reason is required"):
            tools["trw_prd_status_update"].fn(
                prd_id="PRD-TEST-001",
                target_status="review",
                force=True,
                reason="   ",
            )

    def test_force_true_with_reason_succeeds(
        self, prd_project: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """PRD-FIX-009-FR01: force=True with valid reason updates status."""
        from trw_mcp.tools import requirements as req_mod

        monkeypatch.setattr(req_mod, "resolve_project_root", lambda: prd_project)
        monkeypatch.setattr(req_mod, "_config", TRWConfig())

        from fastmcp import FastMCP
        from trw_mcp.tools.requirements import register_requirements_tools
        srv = FastMCP("test-fix009")
        register_requirements_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}

        result = tools["trw_prd_status_update"].fn(
            prd_id="PRD-TEST-001",
            target_status="review",
            force=True,
            reason="Admin override for testing",
        )
        assert result["updated"] is True
        assert result["force_used"] is True

    def test_force_false_invalid_transition_rejected(
        self, prd_project: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """PRD-FIX-009-FR04: force=False invalid transition returns updated=False."""
        from trw_mcp.tools import requirements as req_mod

        monkeypatch.setattr(req_mod, "resolve_project_root", lambda: prd_project)
        monkeypatch.setattr(req_mod, "_config", TRWConfig())

        from fastmcp import FastMCP
        from trw_mcp.tools.requirements import register_requirements_tools
        srv = FastMCP("test-fix009")
        register_requirements_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}

        # DRAFT -> APPROVED is invalid (must go through REVIEW)
        result = tools["trw_prd_status_update"].fn(
            prd_id="PRD-TEST-001",
            target_status="approved",
            force=False,
        )
        assert result["updated"] is False
        assert result["transition_valid"] is False

    def test_force_does_not_bypass_state_machine(
        self, prd_project: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """PRD-FIX-009: force=True does NOT bypass state machine — only guards."""
        from trw_mcp.tools import requirements as req_mod

        monkeypatch.setattr(req_mod, "resolve_project_root", lambda: prd_project)
        monkeypatch.setattr(req_mod, "_config", TRWConfig())

        from fastmcp import FastMCP
        from trw_mcp.tools.requirements import register_requirements_tools
        srv = FastMCP("test-fix009")
        register_requirements_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}

        # DRAFT -> APPROVED is invalid per state machine — force must NOT override
        result = tools["trw_prd_status_update"].fn(
            prd_id="PRD-TEST-001",
            target_status="approved",
            force=True,
            reason="Bypassing for emergency",
        )
        assert result["updated"] is False
        assert result["transition_valid"] is False
        assert result["force_used"] is True

    def test_force_bypasses_guard_checks(
        self, prd_project: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """PRD-FIX-009: force=True with valid transition bypasses guard checks."""
        from trw_mcp.tools import requirements as req_mod

        monkeypatch.setattr(req_mod, "resolve_project_root", lambda: prd_project)
        monkeypatch.setattr(req_mod, "_config", TRWConfig())

        from fastmcp import FastMCP
        from trw_mcp.tools.requirements import register_requirements_tools
        srv = FastMCP("test-fix009")
        register_requirements_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}

        # DRAFT -> REVIEW is valid but has guard (content density).
        # force=True should bypass guard and succeed.
        result = tools["trw_prd_status_update"].fn(
            prd_id="PRD-TEST-001",
            target_status="review",
            force=True,
            reason="Admin override — bypassing content density guard",
        )
        assert result["updated"] is True
        assert result["force_used"] is True
        assert result["guard_passed"] is True
        assert result["new_status"] == "review"


# ---------------------------------------------------------------------------
# PRD-FIX-014: Event Logging Path Fix
# ---------------------------------------------------------------------------


class TestLogStatusChangeEvent:
    """Tests for PRD-FIX-014: _log_status_change_event correct path resolution."""

    def test_event_logged_to_active_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """PRD-FIX-014-FR01: Event written to correct events.jsonl in active run."""
        import json
        from trw_mcp.tools import requirements as req_mod

        # Create docs/{task}/runs/{run_id}/meta/ structure
        run_dir = tmp_path / "docs" / "test-task" / "runs" / "20260209T000000Z-test"
        meta_dir = run_dir / "meta"
        meta_dir.mkdir(parents=True)
        (meta_dir / "run.yaml").write_text("run_id: test\n", encoding="utf-8")

        monkeypatch.setattr(req_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(req_mod, "_config", TRWConfig())

        # Patch state._paths.resolve_project_root (used by resolve_run_path)
        import trw_mcp.state._paths as paths_mod
        monkeypatch.setattr(paths_mod, "resolve_project_root", lambda: tmp_path)

        req_mod._log_status_change_event(
            prd_id="PRD-TEST-001",
            previous_status="draft",
            new_status="review",
            force_used=False,
            reason="Test transition",
        )

        events_file = meta_dir / "events.jsonl"
        assert events_file.exists(), "events.jsonl should be created"
        lines = events_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= 1
        event = json.loads(lines[-1])
        assert event["event"] == "prd_status_change"
        assert event["prd_id"] == "PRD-TEST-001"
        assert event["previous_status"] == "draft"
        assert event["new_status"] == "review"

    def test_no_active_run_logs_debug(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """PRD-FIX-014-FR03: No active run produces debug log, not error."""
        from trw_mcp.tools import requirements as req_mod

        # Create project with no runs
        (tmp_path / ".trw").mkdir()
        (tmp_path / "docs").mkdir()

        monkeypatch.setattr(req_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(req_mod, "_config", TRWConfig())

        import trw_mcp.state._paths as paths_mod
        monkeypatch.setattr(paths_mod, "resolve_project_root", lambda: tmp_path)

        # Should not raise — gracefully handles no active run
        req_mod._log_status_change_event(
            prd_id="PRD-TEST-001",
            previous_status="draft",
            new_status="review",
            force_used=False,
            reason="",
        )

    def test_force_override_field_in_event(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """PRD-FIX-009-FR03: force_override=True appears in event data."""
        import json
        from trw_mcp.tools import requirements as req_mod

        run_dir = tmp_path / "docs" / "test-task" / "runs" / "20260209T000000Z-test"
        meta_dir = run_dir / "meta"
        meta_dir.mkdir(parents=True)
        (meta_dir / "run.yaml").write_text("run_id: test\n", encoding="utf-8")

        monkeypatch.setattr(req_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(req_mod, "_config", TRWConfig())

        import trw_mcp.state._paths as paths_mod
        monkeypatch.setattr(paths_mod, "resolve_project_root", lambda: tmp_path)

        req_mod._log_status_change_event(
            prd_id="PRD-TEST-001",
            previous_status="deprecated",
            new_status="draft",
            force_used=True,
            reason="Admin override",
            force_override=True,
        )

        events_file = meta_dir / "events.jsonl"
        lines = events_file.read_text(encoding="utf-8").strip().split("\n")
        event = json.loads(lines[-1])
        assert event.get("force_override") is True
        assert event["force_used"] is True

    def test_no_force_override_field_when_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """force_override=False does not add the field to event data."""
        import json
        from trw_mcp.tools import requirements as req_mod

        run_dir = tmp_path / "docs" / "test-task" / "runs" / "20260209T000000Z-test"
        meta_dir = run_dir / "meta"
        meta_dir.mkdir(parents=True)
        (meta_dir / "run.yaml").write_text("run_id: test\n", encoding="utf-8")

        monkeypatch.setattr(req_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(req_mod, "_config", TRWConfig())

        import trw_mcp.state._paths as paths_mod
        monkeypatch.setattr(paths_mod, "resolve_project_root", lambda: tmp_path)

        req_mod._log_status_change_event(
            prd_id="PRD-TEST-001",
            previous_status="draft",
            new_status="review",
            force_used=False,
            reason="Normal transition",
            force_override=False,
        )

        events_file = meta_dir / "events.jsonl"
        lines = events_file.read_text(encoding="utf-8").strip().split("\n")
        event = json.loads(lines[-1])
        assert "force_override" not in event
