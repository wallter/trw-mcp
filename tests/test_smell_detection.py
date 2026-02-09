"""Tests for smell detection engine (PRD-CORE-008 Phase 2b)."""

from __future__ import annotations

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.smell_detection import (
    SMELL_PATTERNS,
    SmellPattern,
    detect_smells,
    detect_smells_by_section,
    score_smells,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

_CLEAN_TEXT = """\
The system shall respond to all API requests within 200ms.
The validator shall reject PRDs with ambiguity rates above 5%.
The classifier shall assign one EARS pattern to each functional requirement.
"""

_SMELLY_TEXT = """\
The system should be fast and efficient.
It is designed to be user-friendly and robust.
Various components may be used as appropriate, etc.
TBD
<!-- placeholder -->
"""

_FR_MISSING_ACTOR = """\
## 4. Functional Requirements

### FR01
shall respond to API requests within 200ms.
must return a valid JSON response.

### FR02
The system shall log all errors to the event store.
"""

_COMPOUND_REQ = """\
The system shall validate input and shall log the result to the audit store.
"""


# ---------------------------------------------------------------------------
# Test: Pattern registry
# ---------------------------------------------------------------------------


class TestSmellPatterns:
    """Validate the smell pattern registry."""

    def test_nine_categories(self) -> None:
        categories = {p.category for p in SMELL_PATTERNS}
        assert len(categories) == 9

    def test_all_patterns_compiled(self) -> None:
        for p in SMELL_PATTERNS:
            assert hasattr(p.pattern, "search")

    def test_all_have_suggestions(self) -> None:
        for p in SMELL_PATTERNS:
            assert p.suggestion, f"{p.category} missing suggestion"

    def test_pattern_is_frozen(self) -> None:
        p = SMELL_PATTERNS[0]
        assert isinstance(p, SmellPattern)


# ---------------------------------------------------------------------------
# Test: detect_smells
# ---------------------------------------------------------------------------


class TestDetectSmells:
    """Test the core detect_smells function."""

    def test_clean_text_no_vague_terms(self) -> None:
        findings = detect_smells(_CLEAN_TEXT)
        vague = [f for f in findings if f.category == "vague_terms"]
        assert len(vague) == 0

    def test_smelly_text_finds_vague_terms(self) -> None:
        findings = detect_smells(_SMELLY_TEXT)
        vague = [f for f in findings if f.category == "vague_terms"]
        assert len(vague) >= 3  # fast, efficient, user-friendly, robust, various, etc.

    def test_smelly_text_finds_uncertain_language(self) -> None:
        findings = detect_smells(_SMELLY_TEXT)
        uncertain = [f for f in findings if f.category == "uncertain_language"]
        assert len(uncertain) >= 1  # "should", "may"

    def test_smelly_text_finds_template_content(self) -> None:
        findings = detect_smells(_SMELLY_TEXT)
        template = [f for f in findings if f.category == "template_only_content"]
        assert len(template) >= 1  # TBD, <!-- placeholder -->

    def test_finding_has_line_number(self) -> None:
        findings = detect_smells(_SMELLY_TEXT)
        assert all(f.line_number > 0 for f in findings)

    def test_finding_has_matched_text(self) -> None:
        findings = detect_smells(_SMELLY_TEXT)
        assert all(f.matched_text for f in findings)

    def test_compound_requirement_detected(self) -> None:
        findings = detect_smells(_COMPOUND_REQ)
        compound = [f for f in findings if f.category == "compound_requirements"]
        assert len(compound) >= 1

    def test_passive_voice_detected(self) -> None:
        text = "The input is validated by the system."
        findings = detect_smells(text)
        passive = [f for f in findings if f.category == "passive_voice"]
        assert len(passive) >= 1

    def test_unbounded_scope_detected(self) -> None:
        text = "The system shall handle all requests without any errors."
        findings = detect_smells(text)
        unbounded = [f for f in findings if f.category == "unbounded_scope"]
        assert len(unbounded) >= 1

    def test_dangling_reference_detected(self) -> None:
        text = "See Section 3 for details. As described in the previous section."
        findings = detect_smells(text)
        dangling = [f for f in findings if f.category == "dangling_references"]
        assert len(dangling) >= 1

    def test_missing_quantification_detected(self) -> None:
        text = "The system requires fast response time for all API calls."
        findings = detect_smells(text)
        missing_q = [f for f in findings if f.category == "missing_quantification"]
        assert len(missing_q) >= 1

    def test_headings_skipped(self) -> None:
        text = "# This is a fast heading\nThis line is clean and specific."
        findings = detect_smells(text)
        # Heading line should be skipped
        vague = [f for f in findings if f.category == "vague_terms" and f.line_number == 1]
        assert len(vague) == 0

    def test_code_fences_skipped(self) -> None:
        text = "```\nfast efficient robust\n```"
        findings = detect_smells(text)
        vague = [f for f in findings if f.category == "vague_terms"]
        assert len(vague) == 0

    def test_section_filter_missing_actor(self) -> None:
        # missing_actor only applies to "Functional Requirements" section
        text = "shall do something.\nmust do another thing."
        # Without section context, missing_actor should not fire
        findings = detect_smells(text, section_name="Problem Statement")
        missing_actor = [f for f in findings if f.category == "missing_actor"]
        assert len(missing_actor) == 0

    def test_section_filter_missing_actor_in_fr(self) -> None:
        text = "shall do something.\nmust do another thing."
        findings = detect_smells(text, section_name="Functional Requirements")
        missing_actor = [f for f in findings if f.category == "missing_actor"]
        assert len(missing_actor) >= 1


# ---------------------------------------------------------------------------
# Test: detect_smells_by_section
# ---------------------------------------------------------------------------


class TestDetectSmellsBySection:
    """Test section-aware smell detection."""

    def test_processes_full_prd(self) -> None:
        prd = "---\nprd:\n  id: TEST-001\n---\n\n## 1. Problem Statement\n\nThis is clean.\n"
        findings = detect_smells_by_section(prd)
        assert isinstance(findings, list)

    def test_fr_section_missing_actor(self) -> None:
        prd = (
            "---\nprd:\n  id: TEST-001\n---\n\n"
            "## 1. Problem Statement\n\nClean text.\n\n"
            "## 4. Functional Requirements\n\n"
            "shall validate input.\n"
        )
        findings = detect_smells_by_section(prd)
        missing = [f for f in findings if f.category == "missing_actor"]
        assert len(missing) >= 1


# ---------------------------------------------------------------------------
# Test: score_smells
# ---------------------------------------------------------------------------


class TestScoreSmells:
    """Test the scoring function."""

    def test_clean_prd_high_score(self) -> None:
        clean_prd = (
            "---\nprd:\n  id: TEST-001\n---\n\n"
            "## 1. Problem Statement\n\nThe system shall respond within 200ms.\n"
        )
        dim, findings = score_smells(clean_prd)
        assert dim.name == "smell_score"
        assert dim.max_score == 15.0
        # Clean PRD should score near max
        assert dim.score > 10.0

    def test_smelly_prd_low_score(self) -> None:
        smelly_prd = (
            "---\nprd:\n  id: TEST-001\n---\n\n"
            "## 1. Problem Statement\n\n" + _SMELLY_TEXT
        )
        dim, findings = score_smells(smelly_prd)
        assert dim.score < dim.max_score
        assert len(findings) > 0

    def test_score_never_negative(self) -> None:
        # Very smelly content
        horrible = "TBD\n" * 50 + "should be fast and efficient\n" * 20
        dim, _ = score_smells(horrible)
        assert dim.score >= 0.0

    def test_details_include_counts(self) -> None:
        dim, _ = score_smells(_SMELLY_TEXT)
        assert "total_findings" in dim.details
        assert "error_count" in dim.details
        assert "warning_count" in dim.details
        assert "info_count" in dim.details

    def test_custom_weight(self) -> None:
        config = TRWConfig(validation_smell_weight=30.0)
        clean = "---\nprd:\n  id: X\n---\n\n## 1. Problem Statement\n\nClear requirement.\n"
        dim, _ = score_smells(clean, config=config)
        assert dim.max_score == 30.0

    def test_false_positive_rate(self) -> None:
        """Well-written text should not generate many findings."""
        good_text = (
            "---\nprd:\n  id: TEST-001\n---\n\n"
            "## 1. Problem Statement\n\n"
            "The validation engine shall compute a numeric quality score. "
            "It shall classify each PRD into one of four quality tiers. "
            "The system shall generate improvement suggestions for low-scoring dimensions. "
            "Each dimension scorer shall be implemented as a pure function.\n"
        )
        dim, findings = score_smells(good_text)
        # False positive rate: findings per line of actual content
        content_lines = len([l for l in good_text.split("\n") if l.strip()])
        if content_lines > 0:
            fp_rate = len(findings) / content_lines
            assert fp_rate < 0.5  # reasonable for regex-based detection
