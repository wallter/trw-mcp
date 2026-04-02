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
    hardened = minimal + """
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

    without_subsections = score_structural_completeness(frontmatter, sections, content=minimal)
    with_subsections = score_structural_completeness(frontmatter, sections, content=hardened)

    assert with_subsections.score > without_subsections.score


def test_traceability_rewards_behavior_switch_proof_rows() -> None:
    frontmatter = {
        "traceability": {
            "implements": ["PRD-CORE-001"],
            "depends_on": ["PRD-CORE-002"],
            "enables": ["PRD-CORE-003"],
        }
    }

    basic = _BASE_FRONTMATTER + "# PRD-TEST-900: Hardened PRD\n\n" + _TOP_LEVEL_SECTIONS
    hardened = basic + """
### Behavior Switch Matrix
| Requirement | Old Behavior | New Behavior | Trigger | Code Path | Proof Test |
|-------------|--------------|--------------|---------|-----------|------------|
| FR01 | old | new | init-project | `src/feature.py` | `tests/test_feature.py::test_fr01` |
"""

    assert score_traceability_v2(frontmatter, hardened).score > score_traceability_v2(frontmatter, basic).score


def test_full_validation_penalizes_missing_hardened_dod_surfaces() -> None:
    weak = _BASE_FRONTMATTER + "# PRD-TEST-900: Hardened PRD\n\n" + _TOP_LEVEL_SECTIONS
    strong = weak + """
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

    weak_result = validate_prd_quality_v2(weak)
    strong_result = validate_prd_quality_v2(strong)

    assert strong_result.total_score > weak_result.total_score
