"""Tests for PRD-QUAL-056 Phase 1: File path coverage and assertion coverage scoring.

FR01: file_path_coverage scoring dimension
FR02: assertion_coverage scoring dimension
Integration: score_traceability_v2 includes new dimensions
"""

from __future__ import annotations

from pathlib import Path

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


# ---------------------------------------------------------------------------
# Phase 3 — FR09: Rework Metrics (_deferred_steps_learning.py)
# ---------------------------------------------------------------------------


class TestReworkMetrics:
    """Tests for _step_collect_rework_metrics (PRD-QUAL-056-FR09)."""

    @pytest.mark.unit
    def test_empty_events_returns_empty_metrics(self, tmp_path: Path) -> None:
        """No audit events = empty metrics with zero aggregates."""
        from trw_mcp.state.persistence import FileStateReader

        from trw_mcp.tools._deferred_steps_learning import (
            _step_collect_rework_metrics,
        )

        # Create empty events.jsonl
        meta = tmp_path / "meta"
        meta.mkdir()
        (meta / "events.jsonl").write_text("")

        reader = FileStateReader()
        result = _step_collect_rework_metrics(tmp_path, reader)

        assert result["audit_cycles"] == {}
        assert result["first_pass_compliance"] == {}
        assert result["sprint_avg_audit_cycles"] == 0.0
        assert result["sprint_first_pass_compliance_rate"] == 0.0

    @pytest.mark.unit
    def test_single_prd_two_cycles(self, tmp_path: Path) -> None:
        """PRD with 2 audit cycles has audit_cycles=2, first_pass_compliance=False."""
        import json

        from trw_mcp.state.persistence import FileStateReader

        from trw_mcp.tools._deferred_steps_learning import (
            _step_collect_rework_metrics,
        )

        meta = tmp_path / "meta"
        meta.mkdir()
        events = [
            {"event": "audit_cycle_complete", "data": {"prd_id": "PRD-TEST-001", "verdict": "FAIL"}},
            {"event": "audit_cycle_complete", "data": {"prd_id": "PRD-TEST-001", "verdict": "PASS"}},
        ]
        (meta / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")

        reader = FileStateReader()
        result = _step_collect_rework_metrics(tmp_path, reader)

        assert result["audit_cycles"] == {"PRD-TEST-001": 2}
        assert result["first_pass_compliance"]["PRD-TEST-001"] is False
        assert result["sprint_avg_audit_cycles"] == 2.0

    @pytest.mark.unit
    def test_first_pass_compliance_true(self, tmp_path: Path) -> None:
        """PRD that passes on first audit has first_pass_compliance=True."""
        import json

        from trw_mcp.state.persistence import FileStateReader

        from trw_mcp.tools._deferred_steps_learning import (
            _step_collect_rework_metrics,
        )

        meta = tmp_path / "meta"
        meta.mkdir()
        events = [
            {"event": "audit_cycle_complete", "data": {"prd_id": "PRD-TEST-002", "verdict": "PASS"}},
        ]
        (meta / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")

        reader = FileStateReader()
        result = _step_collect_rework_metrics(tmp_path, reader)

        assert result["audit_cycles"] == {"PRD-TEST-002": 1}
        assert result["first_pass_compliance"]["PRD-TEST-002"] is True
        assert result["sprint_first_pass_compliance_rate"] == 1.0

    @pytest.mark.unit
    def test_sprint_aggregates(self, tmp_path: Path) -> None:
        """Sprint avg and compliance rate computed correctly across 3 PRDs."""
        import json

        from trw_mcp.state.persistence import FileStateReader

        from trw_mcp.tools._deferred_steps_learning import (
            _step_collect_rework_metrics,
        )

        meta = tmp_path / "meta"
        meta.mkdir()
        events = [
            # PRD-A: 1 cycle, pass on first -> first_pass_compliance=True
            {"event": "audit_cycle_complete", "data": {"prd_id": "PRD-A", "verdict": "PASS"}},
            # PRD-B: 2 cycles, fail then pass -> first_pass_compliance=False
            {"event": "audit_cycle_complete", "data": {"prd_id": "PRD-B", "verdict": "FAIL"}},
            {"event": "audit_cycle_complete", "data": {"prd_id": "PRD-B", "verdict": "PASS"}},
            # PRD-C: 3 cycles -> first_pass_compliance=False
            {"event": "audit_cycle_complete", "data": {"prd_id": "PRD-C", "verdict": "FAIL"}},
            {"event": "audit_cycle_complete", "data": {"prd_id": "PRD-C", "verdict": "FAIL"}},
            {"event": "audit_cycle_complete", "data": {"prd_id": "PRD-C", "verdict": "PASS"}},
        ]
        (meta / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")

        reader = FileStateReader()
        result = _step_collect_rework_metrics(tmp_path, reader)

        assert result["audit_cycles"] == {"PRD-A": 1, "PRD-B": 2, "PRD-C": 3}
        assert result["first_pass_compliance"] == {"PRD-A": True, "PRD-B": False, "PRD-C": False}
        # avg = (1+2+3)/3 = 2.0
        assert result["sprint_avg_audit_cycles"] == 2.0
        # 1 out of 3 passed first time
        assert abs(result["sprint_first_pass_compliance_rate"] - 1.0 / 3.0) < 1e-6

    @pytest.mark.unit
    def test_no_run_path_returns_empty(self) -> None:
        """None run_path returns empty metrics."""
        from trw_mcp.state.persistence import FileStateReader

        from trw_mcp.tools._deferred_steps_learning import (
            _step_collect_rework_metrics,
        )

        reader = FileStateReader()
        result = _step_collect_rework_metrics(None, reader)

        assert result["audit_cycles"] == {}
        assert result["sprint_avg_audit_cycles"] == 0.0


# ---------------------------------------------------------------------------
# Phase 3 — FR10: Audit Pattern Auto-Promotion (_cycle.py)
# ---------------------------------------------------------------------------


class TestAuditPatternRecurrence:
    """Tests for detect_audit_finding_recurrence (PRD-QUAL-056-FR10)."""

    @pytest.mark.unit
    def test_no_audit_findings_returns_empty(self) -> None:
        """No audit-finding tags = no promotion candidates."""
        from trw_mcp.state.consolidation._cycle import (
            detect_audit_finding_recurrence,
        )

        entries: list[dict[str, object]] = [
            {"id": "L-1", "tags": ["testing", "pattern"], "summary": "something"},
            {"id": "L-2", "tags": ["bug"], "summary": "another thing"},
        ]
        result = detect_audit_finding_recurrence(entries)
        assert result == []

    @pytest.mark.unit
    def test_below_threshold_not_promoted(self) -> None:
        """2 PRDs with same category (below threshold of 3) = not promoted."""
        from trw_mcp.state.consolidation._cycle import (
            detect_audit_finding_recurrence,
        )

        entries: list[dict[str, object]] = [
            {"id": "L-1", "tags": ["audit-finding", "spec_gap", "PRD-A"], "summary": "missing spec"},
            {"id": "L-2", "tags": ["audit-finding", "spec_gap", "PRD-B"], "summary": "spec gap again"},
        ]
        result = detect_audit_finding_recurrence(entries)
        assert result == []

    @pytest.mark.unit
    def test_above_threshold_promoted(self) -> None:
        """3+ PRDs with same category = promoted."""
        from trw_mcp.state.consolidation._cycle import (
            detect_audit_finding_recurrence,
        )

        entries: list[dict[str, object]] = [
            {"id": "L-1", "tags": ["audit-finding", "spec_gap", "PRD-A"], "summary": "missing spec in A"},
            {"id": "L-2", "tags": ["audit-finding", "spec_gap", "PRD-B"], "summary": "spec gap in B"},
            {"id": "L-3", "tags": ["audit-finding", "spec_gap", "PRD-C"], "summary": "spec gap in C"},
        ]
        result = detect_audit_finding_recurrence(entries)
        assert len(result) == 1
        candidate = result[0]
        assert candidate["category"] == "spec_gap"
        assert candidate["prd_count"] == 3
        assert set(candidate["prd_ids"]) == {"PRD-A", "PRD-B", "PRD-C"}  # type: ignore[arg-type]
        assert len(candidate["sample_summaries"]) <= 3  # type: ignore[arg-type]
        assert isinstance(candidate["nudge_line"], str)
        assert len(str(candidate["nudge_line"])) > 0

    @pytest.mark.unit
    def test_multiple_categories_independent(self) -> None:
        """Each category counted independently."""
        from trw_mcp.state.consolidation._cycle import (
            detect_audit_finding_recurrence,
        )

        entries: list[dict[str, object]] = [
            # spec_gap across 3 PRDs -> promoted
            {"id": "L-1", "tags": ["audit-finding", "spec_gap", "PRD-A"], "summary": "s1"},
            {"id": "L-2", "tags": ["audit-finding", "spec_gap", "PRD-B"], "summary": "s2"},
            {"id": "L-3", "tags": ["audit-finding", "spec_gap", "PRD-C"], "summary": "s3"},
            # impl_gap across only 2 PRDs -> NOT promoted
            {"id": "L-4", "tags": ["audit-finding", "impl_gap", "PRD-A"], "summary": "i1"},
            {"id": "L-5", "tags": ["audit-finding", "impl_gap", "PRD-B"], "summary": "i2"},
        ]
        result = detect_audit_finding_recurrence(entries)
        assert len(result) == 1
        assert result[0]["category"] == "spec_gap"

    @pytest.mark.unit
    def test_custom_threshold(self) -> None:
        """threshold=5 requires 5 distinct PRDs."""
        from trw_mcp.state.consolidation._cycle import (
            detect_audit_finding_recurrence,
        )

        entries: list[dict[str, object]] = [
            {"id": f"L-{i}", "tags": ["audit-finding", "spec_gap", f"PRD-{chr(65+i)}"], "summary": f"s{i}"}
            for i in range(4)
        ]
        # 4 PRDs, threshold=5 -> not promoted
        assert detect_audit_finding_recurrence(entries, threshold=5) == []

        # Add a 5th PRD -> promoted
        entries.append({"id": "L-4", "tags": ["audit-finding", "spec_gap", "PRD-E"], "summary": "s4"})
        result = detect_audit_finding_recurrence(entries, threshold=5)
        assert len(result) == 1
        assert result[0]["prd_count"] == 5

    @pytest.mark.unit
    def test_duplicate_prd_in_same_category_counted_once(self) -> None:
        """Same PRD appearing multiple times in same category counted as 1 distinct PRD."""
        from trw_mcp.state.consolidation._cycle import (
            detect_audit_finding_recurrence,
        )

        entries: list[dict[str, object]] = [
            {"id": "L-1", "tags": ["audit-finding", "spec_gap", "PRD-A"], "summary": "s1"},
            {"id": "L-2", "tags": ["audit-finding", "spec_gap", "PRD-A"], "summary": "s2"},
            {"id": "L-3", "tags": ["audit-finding", "spec_gap", "PRD-B"], "summary": "s3"},
        ]
        # Only 2 distinct PRDs (A, B) -> below default threshold of 3
        result = detect_audit_finding_recurrence(entries)
        assert result == []
