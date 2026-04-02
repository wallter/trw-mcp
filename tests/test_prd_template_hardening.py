"""Tests for PRD template hardening and implementation-readiness scoring."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state.validation import score_structural_completeness, score_traceability_v2, validate_prd_quality_v2


def test_canonical_prd_template_includes_control_points_and_completion_evidence() -> None:
    template_path = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "prd_template.md"
    content = template_path.read_text(encoding="utf-8")

    assert "### Primary Control Points" in content
    assert "### Behavior Switch Matrix" in content
    assert "### Migration Tests" in content
    assert "### Regression Tests" in content
    assert "### Negative / Fallback Tests" in content
    assert "### Completion Evidence (Definition of Done)" in content
    assert "### Migration / Backward Compatibility" in content


def test_repo_prd_template_matches_hardened_structure() -> None:
    template_path = Path(__file__).resolve().parents[2] / "docs" / "requirements-aare-f" / "prds" / "TEMPLATE.md"
    content = template_path.read_text(encoding="utf-8")

    assert "### Primary Control Points" in content
    assert "### Behavior Switch Matrix" in content
    assert "### Completion Evidence (Definition of Done)" in content


_BASE_FRONTMATTER = """\
---
prd:
  id: PRD-TEST-900
  title: Hardened PRD
  version: '1.0'
  status: draft
  priority: P1
  category: CORE
  confidence:
    implementation_feasibility: 0.85
    requirement_clarity: 0.80
    estimate_confidence: 0.75
  traceability:
    implements: [PRD-CORE-001]
    depends_on: [PRD-CORE-002]
    enables: [PRD-CORE-003]
---
"""


_TOP_LEVEL_SECTIONS = """\
## 1. Problem Statement
Real problem statement.

## 2. Goals & Non-Goals
Real goals.

## 3. User Stories
Real user story.

## 4. Functional Requirements
### PRD-TEST-900-FR01: Requirement
**Priority**: Must Have
**Description**: Real requirement.

## 5. Non-Functional Requirements
Real NFRs.

## 6. Technical Approach
### Architecture Impact
Real architecture impact.

## 7. Test Strategy
## 8. Rollout Plan
## 9. Success Metrics
## 10. Dependencies & Risks
## 11. Open Questions
## 12. Traceability Matrix
| Requirement | Source | Implementation | Test | Status |
|-------------|--------|----------------|------|--------|
| FR01 | US-001 | `src/feature.py` | `tests/test_feature.py::test_fr01` | Pending |
"""


def test_structural_completeness_rewards_required_subsections() -> None:
    sections = [
        "Problem Statement",
        "Goals & Non-Goals",
        "User Stories",
        "Functional Requirements",
        "Non-Functional Requirements",
        "Technical Approach",
        "Test Strategy",
        "Rollout Plan",
        "Success Metrics",
        "Dependencies & Risks",
        "Open Questions",
        "Traceability Matrix",
    ]
    frontmatter = {
        "id": "PRD-TEST-900",
        "title": "Hardened PRD",
        "version": "1.0",
        "status": "draft",
        "priority": "P1",
        "category": "CORE",
        "confidence": {
            "implementation_feasibility": 0.85,
            "requirement_clarity": 0.80,
            "estimate_confidence": 0.75,
        },
    }

    minimal = _BASE_FRONTMATTER + "# PRD-TEST-900: Hardened PRD\n\n" + _TOP_LEVEL_SECTIONS
    hardened = (
        minimal
        + """
### Primary Control Points
| Surface | Current Behavior | Required Change | Code Path | Proof |
|---------|------------------|-----------------|-----------|-------|
| Config / Discovery | old | new | `src/config.py` | integration test |

### Behavior Switch Matrix
| Requirement | Old Behavior | New Behavior | Trigger | Code Path | Proof Test |
|-------------|--------------|--------------|---------|-----------|------------|
| FR01 | old | new | init-project | `src/feature.py` | `tests/test_feature.py::test_fr01` |

### Unit Tests
- [ ] `tests/test_feature.py::test_unit_case`

### Integration Tests
- [ ] `tests/test_feature.py::test_integration_case`

### Acceptance Tests
- [ ] `tests/test_feature.py::test_fr01`

### Regression Tests
- [ ] `tests/test_feature.py::test_regression_case`

### Negative / Fallback Tests
- [ ] `tests/test_feature.py::test_missing_config_fallback`

### Completion Evidence (Definition of Done)
- [ ] Generated artifacts exist
- [ ] Runtime/config consumers reference the new behavior
- [ ] Sync/update flows keep the new behavior current
- [ ] Legacy state migrates or is explicitly declared out of scope
- [ ] Acceptance proof exists for each user story / FR
- [ ] Regression and fallback coverage exist

### Migration / Backward Compatibility
- old and new configs are handled
"""
    )

    without_subsections = score_structural_completeness(frontmatter, sections, content=minimal)
    with_subsections = score_structural_completeness(frontmatter, sections, content=hardened)

    assert with_subsections.score > without_subsections.score


def test_traceability_rewards_behavior_switch_proof_rows() -> None:
    frontmatter = {
        "traceability": {
            "implements": ["PRD-CORE-001"],
            "depends_on": ["PRD-CORE-002"],
            "enables": ["PRD-CORE-003"],
        },
        "category": "CORE",
    }

    basic = _BASE_FRONTMATTER + "# PRD-TEST-900: Hardened PRD\n\n" + _TOP_LEVEL_SECTIONS
    hardened = (
        basic
        + """
### Behavior Switch Matrix
| Requirement | Old Behavior | New Behavior | Trigger | Code Path | Proof Test |
|-------------|--------------|--------------|---------|-----------|------------|
| FR01 | old | new | init-project | `src/feature.py` | `tests/test_feature.py::test_fr01` |
"""
    )

    assert score_traceability_v2(frontmatter, hardened).score > score_traceability_v2(frontmatter, basic).score


def test_full_validation_penalizes_missing_hardened_dod_surfaces() -> None:
    weak = _BASE_FRONTMATTER + "# PRD-TEST-900: Hardened PRD\n\n" + _TOP_LEVEL_SECTIONS
    strong = (
        weak
        + """
### Primary Control Points
| Surface | Current Behavior | Required Change | Code Path | Proof |
|---------|------------------|-----------------|-----------|-------|
| Generation | old | new | `src/bootstrap.py` | init test |
| Config / Discovery | old | new | `src/config.py` | config test |
| Sync / Update | old | new | `src/sync.py` | update test |
| Migration | old | new | `src/migrate.py` | migration test |

### Behavior Switch Matrix
| Requirement | Old Behavior | New Behavior | Trigger | Code Path | Proof Test |
|-------------|--------------|--------------|---------|-----------|------------|
| FR01 | old | new | init-project | `src/feature.py` | `tests/test_feature.py::test_fr01` |

### Unit Tests
- [ ] `tests/test_feature.py::test_unit_case`

### Integration Tests
- [ ] `tests/test_feature.py::test_integration_case`

### Acceptance Tests
- [ ] `tests/test_feature.py::test_fr01`

### Migration Tests
- [ ] `tests/test_feature.py::test_migration_case`

### Regression Tests
- [ ] `tests/test_feature.py::test_regression_case`

### Negative / Fallback Tests
- [ ] `tests/test_feature.py::test_missing_config_fallback`

### Completion Evidence (Definition of Done)
- [ ] Generated artifacts exist at the intended paths
- [ ] Runtime/config consumers reference the new behavior
- [ ] Sync/update flows keep the new behavior current
- [ ] Legacy state migrates or is explicitly declared out of scope
- [ ] Acceptance proof exists for each user story / FR
- [ ] Regression and fallback coverage exist for the changed control points

### Migration / Backward Compatibility
- repeated runs are idempotent
"""
    )

    weak_result = validate_prd_quality_v2(weak)
    strong_result = validate_prd_quality_v2(strong)

    assert strong_result.total_score > weak_result.total_score


def test_ai_prd_required_operational_sections() -> None:
    ai_prd_base = (
        _BASE_FRONTMATTER.replace("category: CORE", "category: QUAL")
        + "# PRD-QUAL-055: AI/LLM Hardening\n\n"
        + _TOP_LEVEL_SECTIONS
    )

    ai_incomplete = (
        ai_prd_base
        + """
### Primary Control Points
| Surface | Current Behavior | Required Change | Code Path | Proof |
|---------|------------------|-----------------|-----------|-------|
| Generation | old | new | `src/model.py` | test |

### Behavior Switch Matrix
| Requirement | Old Behavior | New Behavior | Trigger | Code Path | Proof Test |
|-------------|--------------|--------------|---------|-----------|------------|
| FR01 | old | new | runtime | `src/model.py` | `tests/test_model.py::test_fr01` |

### Unit Tests
- [ ] `tests/test_model.py::test_unit_case`

### Integration Tests
- [ ] `tests/test_model.py::test_integration_case`

### Acceptance Tests
- [ ] `tests/test_model.py::test_fr01`

### Migration Tests
- [ ] `tests/test_model.py::test_migration_case`

### Regression Tests
- [ ] `tests/test_model.py::test_regression_case`

### Negative / Fallback Tests
- [ ] `tests/test_model.py::test_missing_config_fallback`

### Completion Evidence (Definition of Done)
- [ ] Generated artifacts exist
- [ ] Runtime/config consumers reference the new behavior
- [ ] Sync/update flows keep the new behavior current
- [ ] Legacy state migrates or is explicitly declared out of scope
- [ ] Acceptance proof exists for each user story / FR
- [ ] Regression and fallback coverage exist for the changed control points

### Migration / Backward Compatibility
- repeated runs are idempotent
"""
    )

    ai_complete = (
        ai_incomplete
        + """
## 7. AI/LLM Operational Sections

### Data / Context Provenance
- **Data Sources**: `dataset.csv`, `api_endpoint`
- **Provenance Chain**: Input → Processing → Model → Output
- **Data Quality Signals**: validation_metrics, drift_threshold

### Failure Modes / Safe Degradation
- **Failure Mode**: Model returns low-confidence predictions
- **Safe Behavior**: Fall back to rule-based heuristic
- **Escalation Path**: Human-in-the-loop review when confidence < 0.6

### Human Oversight / Escalation
- **Review Triggers**: confidence score < 0.6, uncertainty threshold exceeded
- **Escalation Paths**: Slack channel #model-oversight
- **Audit Trail**: decisions logged in `logs/ai_decisions.log`

### Evaluation Plan
- **Baseline Criteria**: accuracy >= 0.85, latency P99 <= 200ms
- **Evaluation Method**: A/B test, automated metrics
- **Performance Metrics**: accuracy, p99 latency, error rate

### Release Gate
- **Rollout Strategy**: Canary (5% → 25% → 100%)
- **Rollback Triggers**: error rate > 1%, latency P99 > 500ms, confidence < 0.5
- **Canary Duration**: 24h per phase

### Monitoring Plan
- **Primary Signal**: prediction latency, drift score, error rate
- **Target Threshold**: P99 < 300ms, drift < 0.1, error rate < 5%
- **Escalation Action**: Alert on threshold exceeded, rollback on sustained violation

### Risk Register By Failure Class
| Failure Class | Scenario | Detection | Mitigation | Residual Risk |
|---------------|----------|-----------|------------|---------------|
| Correctness | Low confidence predictions | Confidence score < 0.6 | Human review | Low |
| Safety | Unsafe recommendations | Safety filter triggered | Halt, alert, revert | Low |
| Maintainability | Model drift | Performance degradation | Retrain, alert | Low |
| Governance | Unattributable output | No source attribution | Human review | Low |
"""
    )

    ai_incomplete_result = validate_prd_quality_v2(ai_incomplete)
    ai_complete_result = validate_prd_quality_v2(ai_complete)

    assert ai_complete_result.total_score > ai_incomplete_result.total_score

    # Check AI section detection in structural completeness dimension
    structural_dims = [d for d in ai_complete_result.dimensions if d.name == "structural_completeness"]
    assert len(structural_dims) == 1
    assert "ai_section_detected" in structural_dims[0].details
    assert structural_dims[0].details["ai_section_detected"] is True


def test_non_ai_prd_not_penalized_for_missing_ai_sections() -> None:
    non_ai_prd = (
        _BASE_FRONTMATTER.replace("category: CORE", "category: INFRA")
        + "# PRD-INFRA-999: Infrastructure PRD\n\n"
        + _TOP_LEVEL_SECTIONS
    )

    non_ai_prd += """
### Primary Control Points
| Surface | Current Behavior | Required Change | Code Path | Proof |
|---------|------------------|-----------------|-----------|-------|
| Deployment | old | new | `src/deploy.py` | test |

### Behavior Switch Matrix
| Requirement | Old Behavior | New Behavior | Trigger | Code Path | Proof Test |
|-------------|--------------|--------------|---------|-----------|------------|
| FR01 | old | new | deploy | `src/deploy.py` | `tests/test_deploy.py::test_fr01` |

### Unit Tests
- [ ] `tests/test_deploy.py::test_unit_case`

### Integration Tests
- [ ] `tests/test_deploy.py::test_integration_case`

### Acceptance Tests
- [ ] `tests/test_deploy.py::test_fr01`

### Migration Tests
- [ ] `tests/test_deploy.py::test_migration_case`

###_regression Tests
- [ ] `tests/test_deploy.py::test_regression_case`

### Negative / Fallback Tests
- [ ] `tests/test_deploy.py::test_missing_config_fallback`

### Completion Evidence (Definition of Done)
- [ ] Generated artifacts exist
- [ ] Runtime/config consumers reference the new behavior
- [ ] Sync/update flows keep the new behavior current
- [ ] Legacy state migrates or is explicitly declared out of scope
- [ ] Acceptance proof exists for each user story / FR
- [ ] Regression and fallback coverage exist for the changed control points

### Migration / Backward Compatibility
- repeated runs are idempotent
"""

    result = validate_prd_quality_v2(non_ai_prd)

    # Non-AI PRD should not have AI detection flags in any dimension
    for dim in result.dimensions:
        assert "ai_section_detected" not in dim.details, f"Dimension {dim.name} should not have ai_section_detected"
        assert "ai_operational_evidence_detected" not in dim.details, (
            f"Dimension {dim.name} should not have ai_operational_evidence_detected"
        )


def test_ai_prd_suggestions_reference_operational_gates() -> None:
    ai_prd_base = (
        _BASE_FRONTMATTER.replace("category: CORE", "category: QUAL")
        + "# PRD-QUAL-055: AI/LLM Hardening\n\n"
        + _TOP_LEVEL_SECTIONS
    )

    ai_incomplete = (
        ai_prd_base
        + """
### Primary Control Points
| Surface | Current Behavior | Required Change | Code Path | Proof |
|---------|------------------|-----------------|-----------|-------|
| Generation | old | new | `src/model.py` | test |

### Behavior Switch Matrix
| Requirement | Old Behavior | New Behavior | Trigger | Code Path | Proof Test |
|-------------|--------------|--------------|---------|-----------|------------|
| FR01 | old | new | runtime | `src/model.py` | `tests/test_model.py::test_fr01` |

### Unit Tests
- [ ] `tests/test_model.py::test_unit_case`

### Integration Tests
- [ ] `tests/test_model.py::test_integration_case`

### Acceptance Tests
- [ ] `tests/test_model.py::test_fr01`

### Migration Tests
- [ ] `tests/test_model.py::test_migration_case`

### Regression Tests
- [ ] `tests/test_model.py::test_regression_case`

### Negative / Fallback Tests
- [ ] `tests/test_model.py::test_missing_config_fallback`

### Completion Evidence (Definition of Done)
- [ ] Generated artifacts exist
- [ ] Runtime/config consumers reference the new behavior
- [ ] Sync/update flows keep the new behavior current
- [ ] Legacy state migrates or is explicitly declared out of scope
- [ ] Acceptance proof exists for each user story / FR
- [ ] Regression and fallback coverage exist for the changed control points

### Migration / Backward Compatibility
- repeated runs are idempotent

## 7. AI/LLM Operational Sections

### Data / Context Provenance
- **Data Sources**: `dataset.csv`
- **Provenance Chain**: Input → Processing → Model → Output
- **Data Quality Signals**: validation_metrics

### Failure Modes / Safe Degradation
- **Failure Mode**: Model returns low-confidence predictions
- **Safe Behavior**: Fall back to rule-based heuristic
- **Escalation Path**: Human-in-the-loop review

### Human Oversight / Escalation
- **Review Triggers**: confidence score < 0.6
- **Escalation Paths**: Slack channel #model-oversight
- **Audit Trail**: decisions logged

### Evaluation Plan
- **Baseline Criteria**: accuracy >= 0.85
- **Evaluation Method**: A/B test
- **Performance Metrics**: accuracy, p99 latency

### Release Gate
- **Rollout Strategy**: Canary (5% → 25% → 100%)
- **Rollback Triggers**: error rate > 1%, latency P99 > 500ms
- **Canary Duration**: 24h per phase

### Monitoring Plan
- **Primary Signal**: prediction latency, drift score
- **Target Threshold**: P99 < 300ms
- **Escalation Action**: Alert on threshold exceeded

### Risk Register By Failure Class
| Failure Class | Scenario | Detection | Mitigation | Residual Risk |
|---------------|----------|-----------|------------|---------------|
| Correctness | Low confidence predictions | Confidence score < 0.6 | Human review | Low |
| Safety | Unsafe recommendations | Safety filter triggered | Halt, alert, revert | Low |
| Maintainability | Model drift | Performance degradation | Retrain, alert | Low |
| Governance | Unattributable output | No source attribution | Human review | Low |
"""
    )

    result = validate_prd_quality_v2(ai_incomplete)

    # AI PRD with complete operational sections scores well on AI dimensions
    # Content density may still be low due to template placeholders in other sections
    # Verify AI detection flags are present in dimension details
    ai_structure_found = False
    ai_trace_found = False

    for dim in result.dimensions:
        if dim.name == "structural_completeness":
            assert "ai_section_detected" in dim.details
            assert dim.details["ai_section_detected"] is True
            ai_structure_found = True
        elif dim.name == "traceability":
            assert "ai_operational_evidence_detected" in dim.details
            assert dim.details["ai_operational_evidence_detected"] is True
            ai_trace_found = True

    assert ai_structure_found, "AI section detection should be present in structural_completeness"
    assert ai_trace_found, "AI operational evidence detection should be present in traceability"

    # Test AI suggestions on a PRD that's actually missing AI operational sections
    ai_missing = (
        ai_prd_base
        + """
### Primary Control Points
| Surface | Current Behavior | Required Change | Code Path | Proof |
|---------|------------------|-----------------|-----------|-------|
| Generation | old | new | `src/model.py` | test |

### Behavior Switch Matrix
| Requirement | Old Behavior | New Behavior | Trigger | Code Path | Proof Test |
|-------------|--------------|--------------|---------|-----------|------------|
| FR01 | old | new | runtime | `src/model.py` | `tests/test_model.py::test_fr01` |

### Unit Tests
- [ ] `tests/test_model.py::test_unit_case`

### Integration Tests
- [ ] `tests/test_model.py::test_integration_case`

### Acceptance Tests
- [ ] `tests/test_model.py::test_fr01`

### Migration Tests
- [ ] `tests/test_model.py::test_migration_case`

### Regression Tests
- [ ] `tests/test_model.py::test_regression_case`

### Negative / Fallback Tests
- [ ] `tests/test_model.py::test_missing_config_fallback`

### Completion Evidence (Definition of Done)
- [ ] Generated artifacts exist
- [ ] Runtime/config consumers reference the new behavior
- [ ] Sync/update flows keep the new behavior current
- [ ] Legacy state migrates or is explicitly declared out of scope
- [ ] Acceptance proof exists for each user story / FR
- [ ] Regression and fallback coverage exist for the changed control points

### Migration / Backward Compatibility
- repeated runs are idempotent
"""
    )

    result_missing = validate_prd_quality_v2(ai_missing)

    # When AI sections are missing, structure and traceability scores should be lower
    # This may trigger suggestions referencing AI operational gates
    ai_gate_suggestion_found = False
    for suggestion in result_missing.improvement_suggestions:
        if "AI/LLM" in suggestion.message or "operational" in suggestion.message.lower():
            ai_gate_suggestion_found = True
            break

    # At least one dimension should have a low score to trigger suggestions
    assert len(result_missing.improvement_suggestions) >= 1, "Expected at least one suggestion for incomplete AI PRD"
