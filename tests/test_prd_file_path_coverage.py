"""Regression tests for bare file/test references in PRD quality scoring."""

from __future__ import annotations

from trw_mcp.state.validation._prd_scoring import (
    _extract_fr_sections,
    _score_file_path_coverage,
    score_implementation_readiness,
    score_traceability_v2,
)

_CONTENT = """\
## 4. Functional Requirements

### FR01: Example Requirement
Implementation: src/foo.py
Test: test_foo.py::test_bar

## 6. Technical Approach

### Primary Control Points
| Surface | Change | Proof |
|---------|--------|-------|
| src/foo.py | Persist new state | test_foo.py::test_bar |

### Behavior Switch Matrix
| Requirement | Old | New | Proof Test |
|-------------|-----|-----|------------|
| FR01 | No persistence | Persists state | test_foo.py::test_bar |

### Key Files
| File | Changes |
|------|---------|
| src/foo.py | Persist new state |

## 7. Test Strategy

### Unit Tests
- test_foo.py::test_bar

### Integration Tests
- tests/test_foo_integration.py::test_bar_flow

### Acceptance Tests
- web/foo.test.tsx

### Regression Tests
- test_foo_regression.py::test_bar_regression

### Negative / Fallback Tests
- test_foo.py::test_bar_invalid

### Completion Evidence (Definition of Done)
- pytest tests/test_foo.py -q

### Migration / Backward Compatibility
- No migration required.

## 12. Traceability Matrix

| Requirement | Source | Implementation | Test | Status |
|-------------|--------|----------------|------|--------|
| FR01 | US-001 | src/foo.py | test_foo.py::test_bar | Pending |
"""

_FRONTMATTER = {
    "category": "CORE",
    "traceability": {
        "implements": ["PRD-CORE-001"],
        "depends_on": ["PRD-CORE-000"],
        "enables": ["PRD-CORE-002"],
    },
}


def test_file_path_coverage_counts_bare_refs() -> None:
    fr_sections = _extract_fr_sections(_CONTENT)

    coverage = _score_file_path_coverage(_CONTENT, fr_sections)

    assert coverage == 1.0


def test_implementation_readiness_counts_bare_refs() -> None:
    score = score_implementation_readiness(_FRONTMATTER, _CONTENT)

    assert score.details["implementation_refs"] == 1
    assert score.details["test_refs"] >= 4


def test_traceability_matrix_counts_bare_refs() -> None:
    score = score_traceability_v2(_FRONTMATTER, _CONTENT)

    assert score.details["matrix_score"] > 0.0
    assert score.details["proof_score"] > 0.0
