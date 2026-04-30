"""Shared fixtures for split FIX-056 status integrity tests."""

from __future__ import annotations

_FRONTMATTER_TMPL = """\
---
prd:
  id: PRD-TEST-001
  title: Test PRD
  version: '1.0'
  status: {status}
  priority: P1
  category: TEST
  approved_by: {approved_by}
  partially_implemented_frs: {partial_frs}
  evidence:
    level: moderate
    sources: []
  confidence:
    implementation_feasibility: 0.8
    requirement_clarity: 0.8
    estimate_confidence: 0.7
    test_coverage_target: 0.85
  traceability:
    implements: [PRD-CORE-001]
    depends_on: []
    enables: []
    conflicts_with: []
  metrics:
    success_criteria: []
    measurement_method: []
  quality_gates:
    ambiguity_rate_max: 0.05
    completeness_min: 0.85
    traceability_coverage_min: 0.9
    consistency_validation_min: 0.95
  dates:
    created: '2026-03-13'
    updated: '2026-03-13'
---
"""

_BODY_TMPL = """\
# PRD-TEST-001: Test PRD

**Quick Reference**:
- **Status**: {prose_status}
- **Priority**: P1
- **Evidence**: Moderate
- **Implementation Confidence**: 0.8

---

## 1. Problem Statement

### Background
Test background.

### Problem
Test problem statement here with enough text to score well.

### Impact
Test impact analysis here.

## 2. Goals & Non-Goals

### Goals
- [x] G1: Test goal one here
- [x] G2: Test goal two here

### Non-Goals
- Not doing X.
- Not doing Y.

## 3. User Stories

### US-001: Basic
**As a** user **I want** something **So that** value.

**Acceptance Criteria**:
- [x] Given state, When action, Then outcome.

## 4. Functional Requirements

{fr_content}

## 5. Non-Functional Requirements

### PRD-TEST-001-NFR01: Performance
- Response time < 200ms p99.

### PRD-TEST-001-NFR02: Reliability
- 99.9% availability.

### PRD-TEST-001-NFR03: Security
- Auth required.

## 6. Technical Approach

### Architecture Impact
None.

### Key Files
| File | Changes |
|------|---------|
| `test.py` | Test changes |

## 7. Test Strategy

### Unit Tests
- `test_fix056_status_integrity.py::test_status_drift_detection`

## 8. Rollout Plan

### Phase 1
- Deploy.

## 9. Success Metrics

| Metric | Baseline | Target |
|--------|----------|--------|
| Zero drift | 30% | 0% |

## 10. Dependencies & Risks

### Dependencies
| ID | Description | Status | Blocking |
|----|-------------|--------|----------|
| DEP-001 | None | Resolved | No |

### Risks
| ID | Risk | Probability | Impact | Mitigation | Residual Risk |
|----|------|-------------|--------|------------|---------------|
| RISK-001 | Low | Low | Low | None needed | Low |

## 11. Open Questions

- [x] OQ-001: None. `[blocking: no]`

## 12. Traceability Matrix

| Requirement | Source | Implementation | Test | Status |
|-------------|--------|----------------|------|--------|
| FR01 | Audit | `prd_quality.py` | `test_fix056_status_integrity.py` | Pending |

"""

_FR_WITH_STATUS = """\
### PRD-TEST-001-FR01: First Requirement
**Priority**: Must Have
**Status**: active
**Description**: A well-defined requirement.
**Acceptance**: Given X, When Y, Then Z.
**Dependencies**: None
**Confidence**: 0.9
"""

_FR_WITHOUT_STATUS = """\
### PRD-TEST-001-FR01: First Requirement
**Priority**: Must Have
**Description**: A well-defined requirement.
**Acceptance**: Given X, When Y, Then Z.
**Dependencies**: None
**Confidence**: 0.9
"""


def _make_prd(
    *,
    fm_status: str = "draft",
    prose_status: str = "Draft",
    approved_by: str = "null",
    partial_frs: str = "[]",
    fr_content: str = _FR_WITH_STATUS,
) -> str:
    """Build a minimal PRD string for FIX-056 status integrity tests."""
    frontmatter = _FRONTMATTER_TMPL.format(
        status=fm_status,
        approved_by=approved_by,
        partial_frs=partial_frs,
    )
    body = _BODY_TMPL.format(prose_status=prose_status, fr_content=fr_content)
    return frontmatter + body
