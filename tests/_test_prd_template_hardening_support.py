"""Shared helpers for PRD template hardening test splits."""

from __future__ import annotations

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

_REQUIRED_SECTIONS = [
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


def _make_prd(*, prd_id: str, title: str, category: str) -> str:
    return (
        _BASE_FRONTMATTER.replace("PRD-TEST-900", prd_id)
        .replace("Hardened PRD", title)
        .replace("category: CORE", f"category: {category}")
        + f"# {prd_id}: {title}\n\n"
        + _TOP_LEVEL_SECTIONS.replace("PRD-TEST-900", prd_id)
    )


def _assert_no_ai_detection_flags(result: object) -> None:
    for dim in result.dimensions:
        assert "ai_section_detected" not in dim.details, f"Dimension {dim.name} should not have ai_section_detected"
        assert "ai_operational_evidence_detected" not in dim.details, (
            f"Dimension {dim.name} should not have ai_operational_evidence_detected"
        )
