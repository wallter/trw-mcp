"""Shared fixtures and helpers for validation v2 tests."""

from __future__ import annotations

import textwrap

_MINIMAL_FRONTMATTER = """\
---
prd:
  id: PRD-TEST-001
  title: Test PRD
  version: '1.0'
  status: draft
  priority: P1
  category: CORE
  confidence:
    implementation_feasibility: 0.85
    requirement_clarity: 0.80
    estimate_confidence: 0.75
  traceability:
    implements: []
    depends_on: [PRD-CORE-007]
    enables: [PRD-CORE-009]
---
"""

_SKELETON_PRD = (
    _MINIMAL_FRONTMATTER
    + """\
# PRD-TEST-001: Test PRD

## 1. Problem Statement
<!-- Describe the problem -->

## 2. Goals & Non-Goals
<!-- List goals -->

## 3. User Stories
<!-- User stories -->

## 4. Functional Requirements
<!-- Requirements -->

## 5. Non-Functional Requirements
<!-- NFRs -->

## 6. Technical Approach
<!-- Architecture -->

## 7. Test Strategy
<!-- Tests -->

## 8. Rollout Plan
<!-- Rollout -->

## 9. Success Metrics
<!-- Metrics -->

## 10. Dependencies & Risks
<!-- Dependencies -->

## 11. Open Questions
<!-- Questions -->

## 12. Traceability Matrix
<!-- Matrix -->
"""
)

_FILLED_PRD = (
    _MINIMAL_FRONTMATTER
    + """\
# PRD-TEST-001: Test PRD

## 1. Problem Statement

### Background
The current system lacks proper validation. Users report frequent errors
when submitting forms. The error handling module in src/errors.py has not
been updated since version 1.0 and does not cover the new API endpoints.

### Problem
Form validation fails silently, causing data corruption in 5% of submissions.

### Impact
Users lose trust in the system. Support tickets increased 40% in Q1.

## 2. Goals & Non-Goals

### Goals
- Implement comprehensive form validation with specific error messages
- Reduce data corruption rate from 5% to below 0.1%
- Add validation feedback within 200ms response time

### Non-Goals
- Redesigning the entire form UI
- Migrating to a new validation library

## 3. User Stories

### US-001: Form Validation Feedback
**As a** user
**I want** to see specific validation errors when I submit a form
**So that** I can fix my input without guessing what went wrong

**Acceptance Criteria**:
- Given invalid email format, When submitted, Then show "Invalid email format"
- Given missing required field, When submitted, Then highlight the field in red

## 4. Functional Requirements

### PRD-TEST-001-FR01: Input Validation
**Priority**: Must Have
**Description**: When the user submits a form, the system shall validate all
required fields and return specific error messages for each invalid field.
The validation shall complete within 200ms.
**Acceptance**: All required fields are validated. Error messages are specific.

### PRD-TEST-001-FR02: Error Display
**Priority**: Must Have
**Description**: The system shall display validation errors inline next to
the corresponding form fields. Each error message shall be descriptive
and suggest how to fix the issue.
**Acceptance**: Errors appear next to fields. Messages include fix suggestions.

## 5. Non-Functional Requirements

### NFR01: Performance
- Form validation shall complete within 200ms for forms with up to 50 fields
- No external API calls during client-side validation

### NFR02: Accessibility
- Error messages shall be readable by screen readers
- Color is not the only indicator of errors (also uses icons and text)

## 6. Technical Approach

### Architecture Impact
This change modifies the validation middleware in src/validation.py and adds
a new error display component in src/components/ErrorDisplay.tsx.

### Key Files
| File | Changes |
|------|---------|
| `src/validation.py` | Add field-level validation rules |
| `src/components/ErrorDisplay.tsx` | New inline error component |

## 7. Test Strategy

### Unit Tests
- test_validate_required_field_missing
- test_validate_email_format_invalid
- test_validate_within_200ms
- test_error_display_renders_message

### Integration Tests
- test_form_submission_with_errors
- test_form_submission_all_valid

## 8. Rollout Plan

### Phase 1: Validation Logic (1 session)
1. Add validation rules to src/validation.py
2. Write 10 unit tests
3. Verify 200ms performance target

### Phase 2: Error Display (1 session)
1. Create ErrorDisplay component
2. Wire into form submission flow
3. Write integration tests

## 9. Success Metrics

| Metric | Target | Method |
|--------|--------|--------|
| Data corruption rate | <0.1% | Monitor submissions |
| Validation latency | <200ms | Performance tests |
| Support tickets | -30% | Ticket count |

## 10. Dependencies & Risks

### Dependencies
| ID | Description | Status | Blocking |
|----|-------------|--------|----------|
| DEP-001 | React 18+ for concurrent rendering | Available | No |

### Risks
| ID | Risk | Probability | Impact | Mitigation |
|----|------|-------------|--------|------------|
| RISK-001 | Validation rules incomplete | Medium | High | Incremental rollout |

## 11. Open Questions

- Should we validate on blur or only on submit?
- How do we handle dynamic form fields added via JavaScript?

## 12. Traceability Matrix

| Requirement | Source | Implementation | Test | Status |
|-------------|--------|----------------|------|--------|
| FR01 (Input Validation) | US-001 | `src/validation.py:validate_form()` | `test_validate_required_field_missing` | Pending |
| FR02 (Error Display) | US-001 | `src/components/ErrorDisplay.tsx` | `test_error_display_renders_message` | Pending |
"""
)

_PARTIAL_PRD = (
    _MINIMAL_FRONTMATTER
    + """\
# PRD-TEST-001: Test PRD

## 1. Problem Statement

### Background
The system needs better error handling. This is important for reliability.

### Problem
Errors are not handled consistently across modules.

## 2. Goals & Non-Goals

### Goals
- Improve error handling across the codebase

## 3. User Stories
<!-- TODO: Add user stories -->

## 4. Functional Requirements

### PRD-TEST-001-FR01: Error Handler
**Priority**: Must Have
**Description**: The system shall handle errors consistently.

## 5. Non-Functional Requirements
<!-- TODO -->

## 6. Technical Approach
<!-- TODO -->

## 7. Test Strategy
- test_error_handling

## 8. Rollout Plan
<!-- TODO -->

## 9. Success Metrics
<!-- TODO -->

## 10. Dependencies & Risks
<!-- TODO -->

## 11. Open Questions
- What error handling strategy should we use?

## 12. Traceability Matrix
<!-- TODO: Fill in matrix -->
    """
)


def _build_integrity_prd(*, prd_id: str, title: str, category: str, path_ref: str) -> str:
    """Build a small, structurally valid PRD for integrity tests."""
    return textwrap.dedent(
        f"""\
        ---
        prd:
          id: {prd_id}
          title: {title}
          version: '1.0'
          status: draft
          priority: P1
          category: {category}
        confidence:
          implementation_feasibility: 0.8
          requirement_clarity: 0.8
          estimate_confidence: 0.7
        traceability:
          implements: []
          depends_on: []
        ---

        # {prd_id}: {title}

        ## 1. Problem Statement
        The contract drifts without explicit validation.

        ## 2. Goals & Non-Goals
        Tighten validation and keep docs aligned.

        ## 3. User Stories
        As a maintainer, I want integrity checks.

        ## 4. Functional Requirements
        Validation shall cover `{path_ref}`.

        ## 5. Non-Functional Requirements
        Validation shall stay fast and deterministic.

        ## 6. Technical Approach
        Use `{path_ref}` as the implementation control point.

        ## 7. Test Strategy
        Add tests for `{path_ref}`.

        ## 8. Rollout Plan
        Roll out behind focused pytest coverage.

        ## 9. Success Metrics
        Validation rejects bad evidence.

        ## 10. Dependencies & Risks
        Depends on `{path_ref}`.

        ## 11. Open Questions
        None for this test fixture.

        ## 12. Traceability Matrix
        | Requirement | Implementation | Test |
        |-------------|----------------|------|
        | FR01 | `{path_ref}` | `tests/test_integrity.py::test_case` |
        """
    )


def extract_all_12_section_names() -> list[str]:
    """Return list of 12 expected AARE-F section names."""
    return [
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
