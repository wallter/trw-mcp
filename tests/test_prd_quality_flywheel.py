"""Focused anti-Goodhart tests for PRD quality scoring.

These tests lock in the behavior added by PRD-QUAL-059:
- proof-rich implementation plans score above filler-heavy prose
- implementation-readiness guidance outranks density nudges
- density remains a hygiene signal rather than the primary flywheel
"""

from __future__ import annotations

from trw_mcp.models.requirements import DimensionScore
from trw_mcp.state.validation import (
    generate_improvement_suggestions,
    score_implementation_readiness,
)
from trw_mcp.state.validation._prd_scoring import (
    _extract_fr_sections,
    _score_assertion_coverage,
    _score_file_path_coverage,
    score_traceability_v2,
)


_FRONTMATTER = {"category": "CORE"}

_PROOF_RICH_CONTENT = """\
## 4. Functional Requirements

### PRD-TEST-001-FR01: Toggle
The system shall update the toggle state and persist the new value.

## 6. Technical Approach

### Primary Control Points
| Surface | Change | Proof |
|---------|--------|-------|
| `src/service.py` | Persist the new toggle state | `test_toggle.py::test_toggle_persists` |

### Behavior Switch Matrix
| Requirement | Old | New | Proof Test |
|-------------|-----|-----|------------|
| FR01 | Toggle updates memory only | Toggle updates memory and storage | `test_toggle.py::test_toggle_persists` |

### Key Files
| File | Changes |
|------|---------|
| `src/service.py` | Persist toggle state |

## 7. Test Strategy

### Unit Tests
- `test_toggle.py::test_toggle_persists`

### Integration Tests
- `test_api.py::test_toggle_endpoint`

### Acceptance Tests
- `platform/src/toggle.test.tsx`

### Regression Tests
- `test_toggle.py::test_toggle_regression`

### Negative / Fallback Tests
- `test_toggle.py::test_toggle_invalid_state`

### Completion Evidence (Definition of Done)
- `pytest tests/test_toggle.py -q`

### Migration / Backward Compatibility
- No migration required.
"""

_FILLER_HEAVY_CONTENT = """\
## 4. Functional Requirements

### PRD-TEST-001-FR01: Toggle
The system shall improve the toggle experience in a comprehensive and
well-structured way that provides meaningful improvements for users.

## 6. Technical Approach

This section describes the overall approach in broad terms. The implementation
should be thoughtful, consistent, and aligned with the broader system goals.
The final solution should be maintainable and reliable.

## 7. Test Strategy

The solution should be tested thoroughly with appropriate unit, integration,
and regression testing as needed for confidence in the outcome.
"""

_TRACEABILITY_ONLY_PATHS = """\
## 4. Functional Requirements

### FR01: Example Requirement
This FR intentionally omits file paths in the prose.

## 12. Traceability Matrix

| Requirement | Source | Implementation | Test | Status |
|-------------|--------|----------------|------|--------|
| FR01 | US-001 | src/foo.py | test_foo.py::test_bar | Pending |
"""

_ASSERTION_BLOCK_CONTENT = """\
## 4. Functional Requirements

### FR01: Covered by assertions block
Implementation: src/foo.py
Test: test_foo.py::test_bar
```assertions
grep_present: "src/foo.py"
```

### FR02: Only prose mention
Implementation: src/bar.py
Test: test_bar.py::test_baz
This section mentions grep_present as documentation, not as an assertion block.
"""


def test_implementation_readiness_prefers_proof_rich_content() -> None:
    proof_rich = score_implementation_readiness(_FRONTMATTER, _PROOF_RICH_CONTENT)
    filler_heavy = score_implementation_readiness(_FRONTMATTER, _FILLER_HEAVY_CONTENT)

    assert proof_rich.name == "implementation_readiness"
    assert proof_rich.score > filler_heavy.score
    assert proof_rich.details["test_refs"] > filler_heavy.details["test_refs"]
    assert proof_rich.details["implementation_refs"] > filler_heavy.details["implementation_refs"]


def test_density_guidance_is_hygiene_not_primary_driver() -> None:
    dims = [
        DimensionScore(name="content_density", score=11.0, max_score=20.0),
        DimensionScore(name="implementation_readiness", score=8.0, max_score=25.0),
        DimensionScore(name="traceability", score=12.0, max_score=35.0),
    ]

    suggestions = generate_improvement_suggestions(dims)
    suggestion_dimensions = [suggestion.dimension for suggestion in suggestions]

    assert "content_density" not in suggestion_dimensions
    assert suggestion_dimensions[0] == "implementation_readiness"


def test_implementation_readiness_message_mentions_executable_proof() -> None:
    dims = [DimensionScore(name="implementation_readiness", score=5.0, max_score=25.0)]

    suggestions = generate_improvement_suggestions(dims)

    assert len(suggestions) == 1
    assert "control points" in suggestions[0].message
    assert "proof tests" in suggestions[0].message


def test_file_path_coverage_scoring() -> None:
    fr_sections = _extract_fr_sections(_TRACEABILITY_ONLY_PATHS)

    coverage = _score_file_path_coverage(_TRACEABILITY_ONLY_PATHS, fr_sections)
    traceability = score_traceability_v2(_FRONTMATTER, _TRACEABILITY_ONLY_PATHS)

    assert coverage == 1.0
    assert traceability.details["file_path_coverage"] == 1.0


def test_assertion_coverage_scoring() -> None:
    fr_sections = _extract_fr_sections(_ASSERTION_BLOCK_CONTENT)

    coverage = _score_assertion_coverage(_ASSERTION_BLOCK_CONTENT, fr_sections)
    traceability = score_traceability_v2(_FRONTMATTER, _ASSERTION_BLOCK_CONTENT)

    assert coverage == 0.5
    assert traceability.details["assertion_coverage"] == 0.5
    assert "suggestions" not in traceability.details
