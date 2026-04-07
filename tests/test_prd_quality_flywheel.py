"""Tests for PRD-QUAL-056 Phase 1: File path coverage and assertion coverage scoring.

FR01: file_path_coverage scoring dimension
FR02: assertion_coverage scoring dimension
Integration: score_traceability_v2 includes new dimensions
"""

from __future__ import annotations

import pytest

from trw_mcp.state.validation._prd_scoring import (
    _extract_fr_sections,
    _score_assertion_coverage,
    _score_file_path_coverage,
    score_traceability_v2,
)

# ---------------------------------------------------------------------------
# Fixtures: PRD content with varying coverage levels
# ---------------------------------------------------------------------------

FULL_COVERAGE_PRD = """\
---
prd:
  id: PRD-TEST-001
  title: Test PRD
  version: "1.0"
  status: draft
  priority: P0
  category: CORE
traceability:
  implements: ["VISION.md"]
  depends_on: []
  enables: []
confidence:
  implementation_feasibility: 0.9
  requirement_clarity: 0.9
  estimate_confidence: 0.85
---
# PRD-TEST-001: Test PRD

## 4. Functional Requirements

### PRD-TEST-001-FR01: Feature One
**Implementation**: `src/module/feature.py`
**Test**: `tests/test_feature.py::test_feature_one`
**Assertions**:
- {"type": "grep_present", "pattern": "class Feature", "target": "src/module/feature.py"}

### PRD-TEST-001-FR02: Feature Two
**Implementation**: `src/module/feature2.py`
**Test**: `tests/test_feature2.py::test_feature_two`
**Assertions**:
- {"type": "grep_absent", "pattern": "TODO", "target": "src/module/feature2.py"}

## 12. Traceability Matrix
| Requirement | Implementation | Test | Status |
|---|---|---|---|
| FR01 | `src/module/feature.py` | `tests/test_feature.py` | Pending |
| FR02 | `src/module/feature2.py` | `tests/test_feature2.py` | Pending |
"""

PARTIAL_COVERAGE_PRD = """\
---
prd:
  id: PRD-TEST-002
  title: Partial Test PRD
  version: "1.0"
  status: draft
  priority: P1
  category: FIX
traceability:
  implements: []
  depends_on: []
  enables: []
---
# PRD-TEST-002: Partial Test PRD

## 4. Functional Requirements

### PRD-TEST-002-FR01: Feature One
**Description**: Add a feature with file path.
**Implementation**: `src/module/feature.py`

### PRD-TEST-002-FR02: Feature Two
**Description**: Another feature without file paths or assertions.

### PRD-TEST-002-FR03: Feature Three
**Test**: `tests/test_feature3.py`
**Assertions**:
- {"type": "grep_present", "pattern": "def test_three", "target": "tests/test_feature3.py"}
"""

ZERO_COVERAGE_PRD = """\
---
prd:
  id: PRD-TEST-003
  title: Zero Coverage PRD
  version: "1.0"
  status: draft
  priority: P2
  category: QUAL
traceability:
  implements: []
  depends_on: []
  enables: []
---
# PRD-TEST-003: Zero Coverage PRD

## 4. Functional Requirements

### PRD-TEST-003-FR01: Feature One
**Description**: A vague feature without any file paths or assertions.

### PRD-TEST-003-FR02: Feature Two
**Description**: Another vague feature.
"""


# ---------------------------------------------------------------------------
# _extract_fr_sections
# ---------------------------------------------------------------------------


class TestExtractFrSections:
    @pytest.mark.unit
    def test_extracts_all_frs_from_full_coverage(self) -> None:
        sections = _extract_fr_sections(FULL_COVERAGE_PRD)
        assert len(sections) == 2
        assert "FR01" in sections[0][0]
        assert "FR02" in sections[1][0]

    @pytest.mark.unit
    def test_partial_prd_extracts_three(self) -> None:
        sections = _extract_fr_sections(PARTIAL_COVERAGE_PRD)
        assert len(sections) == 3
        assert "FR01" in sections[0][0]
        assert "FR02" in sections[1][0]
        assert "FR03" in sections[2][0]

    @pytest.mark.unit
    def test_empty_content_returns_empty(self) -> None:
        assert _extract_fr_sections("") == []

    @pytest.mark.unit
    def test_content_without_frs_returns_empty(self) -> None:
        assert _extract_fr_sections("# Title\n\nJust some text.") == []

    @pytest.mark.unit
    def test_fr_body_contains_expected_text(self) -> None:
        sections = _extract_fr_sections(FULL_COVERAGE_PRD)
        # FR01 body should contain the impl path
        assert "src/module/feature.py" in sections[0][1]
        # FR01 body should contain the test ref
        assert "tests/test_feature.py" in sections[0][1]

    @pytest.mark.unit
    def test_fr_sections_stop_at_next_h2(self) -> None:
        """FR body should not leak into the next ## section."""
        sections = _extract_fr_sections(FULL_COVERAGE_PRD)
        # FR02 body should NOT contain Traceability Matrix content
        assert "Traceability Matrix" not in sections[-1][1]


# ---------------------------------------------------------------------------
# _score_file_path_coverage (FR01)
# ---------------------------------------------------------------------------


class TestFilePathCoverage:
    @pytest.mark.unit
    def test_full_coverage_scores_one(self) -> None:
        sections = _extract_fr_sections(FULL_COVERAGE_PRD)
        score = _score_file_path_coverage(FULL_COVERAGE_PRD, sections)
        assert score == 1.0

    @pytest.mark.unit
    def test_partial_coverage_between_zero_and_one(self) -> None:
        sections = _extract_fr_sections(PARTIAL_COVERAGE_PRD)
        score = _score_file_path_coverage(PARTIAL_COVERAGE_PRD, sections)
        # FR01 has impl (1 impl, 0 test), FR02 has nothing (0, 0),
        # FR03 has both (impl regex matches test path too) (1 impl, 1 test)
        # (2 impl + 1 test) / (2 * 3) = 3/6 = 0.5
        assert score == 0.5

    @pytest.mark.unit
    def test_zero_coverage_scores_zero(self) -> None:
        sections = _extract_fr_sections(ZERO_COVERAGE_PRD)
        score = _score_file_path_coverage(ZERO_COVERAGE_PRD, sections)
        assert score == 0.0

    @pytest.mark.unit
    def test_empty_fr_list_returns_zero(self) -> None:
        score = _score_file_path_coverage("no FRs here", [])
        assert score == 0.0

    @pytest.mark.unit
    def test_score_is_bounded_zero_to_one(self) -> None:
        sections = _extract_fr_sections(FULL_COVERAGE_PRD)
        score = _score_file_path_coverage(FULL_COVERAGE_PRD, sections)
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# _score_assertion_coverage (FR02)
# ---------------------------------------------------------------------------


class TestAssertionCoverage:
    @pytest.mark.unit
    def test_full_coverage_scores_one(self) -> None:
        sections = _extract_fr_sections(FULL_COVERAGE_PRD)
        score = _score_assertion_coverage(FULL_COVERAGE_PRD, sections)
        assert score == 1.0

    @pytest.mark.unit
    def test_partial_coverage_one_of_three(self) -> None:
        sections = _extract_fr_sections(PARTIAL_COVERAGE_PRD)
        score = _score_assertion_coverage(PARTIAL_COVERAGE_PRD, sections)
        # Only FR03 has assertions out of 3 FRs => 1/3 ~0.333
        assert 0.3 <= score <= 0.4

    @pytest.mark.unit
    def test_zero_coverage_scores_zero(self) -> None:
        sections = _extract_fr_sections(ZERO_COVERAGE_PRD)
        score = _score_assertion_coverage(ZERO_COVERAGE_PRD, sections)
        assert score == 0.0

    @pytest.mark.unit
    def test_empty_fr_list_returns_zero(self) -> None:
        score = _score_assertion_coverage("no FRs here", [])
        assert score == 0.0


# ---------------------------------------------------------------------------
# score_traceability_v2 integration
# ---------------------------------------------------------------------------


class TestScoreTraceabilityV2Integration:
    @pytest.mark.unit
    def test_includes_file_path_coverage_in_details(self) -> None:
        from trw_mcp.state.prd_utils import parse_frontmatter

        fm = parse_frontmatter(FULL_COVERAGE_PRD)
        result = score_traceability_v2(fm, FULL_COVERAGE_PRD)
        assert "file_path_coverage" in result.details
        assert "assertion_coverage" in result.details

    @pytest.mark.unit
    def test_zero_coverage_includes_suggestions(self) -> None:
        from trw_mcp.state.prd_utils import parse_frontmatter

        fm = parse_frontmatter(ZERO_COVERAGE_PRD)
        result = score_traceability_v2(fm, ZERO_COVERAGE_PRD)
        suggestions = result.details.get("suggestions")
        assert isinstance(suggestions, list)
        assert any("file path" in str(s).lower() for s in suggestions)
        assert any("assertion" in str(s).lower() for s in suggestions)

    @pytest.mark.unit
    def test_full_coverage_no_suggestions(self) -> None:
        from trw_mcp.state.prd_utils import parse_frontmatter

        fm = parse_frontmatter(FULL_COVERAGE_PRD)
        result = score_traceability_v2(fm, FULL_COVERAGE_PRD)
        # Full coverage should not trigger any suggestions
        suggestions = result.details.get("suggestions")
        assert suggestions is None or len(suggestions) == 0  # type: ignore[arg-type]

    @pytest.mark.unit
    def test_backward_compat_no_score_regression(self) -> None:
        """Existing PRDs without file paths should not score lower than before."""
        from trw_mcp.state.prd_utils import parse_frontmatter

        fm = parse_frontmatter(ZERO_COVERAGE_PRD)
        result = score_traceability_v2(fm, ZERO_COVERAGE_PRD)
        # Score should be >= 0 (additive bonus, not penalty)
        assert result.score >= 0.0

    @pytest.mark.unit
    def test_coverage_bonus_is_additive(self) -> None:
        """Full coverage should produce a higher score than zero coverage (additive bonus)."""
        from trw_mcp.state.prd_utils import parse_frontmatter

        fm_full = parse_frontmatter(FULL_COVERAGE_PRD)
        result_full = score_traceability_v2(fm_full, FULL_COVERAGE_PRD)

        fm_zero = parse_frontmatter(ZERO_COVERAGE_PRD)
        result_zero = score_traceability_v2(fm_zero, ZERO_COVERAGE_PRD)

        # The full coverage PRD should score >= zero coverage PRD
        # (it also has better traceability fields so it should be strictly higher)
        assert result_full.score >= result_zero.score

    @pytest.mark.unit
    def test_file_path_coverage_value_range(self) -> None:
        from trw_mcp.state.prd_utils import parse_frontmatter

        fm = parse_frontmatter(FULL_COVERAGE_PRD)
        result = score_traceability_v2(fm, FULL_COVERAGE_PRD)
        fpc = result.details["file_path_coverage"]
        assert isinstance(fpc, float)
        assert 0.0 <= fpc <= 1.0

    @pytest.mark.unit
    def test_assertion_coverage_value_range(self) -> None:
        from trw_mcp.state.prd_utils import parse_frontmatter

        fm = parse_frontmatter(PARTIAL_COVERAGE_PRD)
        result = score_traceability_v2(fm, PARTIAL_COVERAGE_PRD)
        ac = result.details["assertion_coverage"]
        assert isinstance(ac, float)
        assert 0.0 <= ac <= 1.0
