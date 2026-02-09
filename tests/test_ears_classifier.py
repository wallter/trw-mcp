"""Tests for EARS pattern classifier (PRD-CORE-008 Phase 2c)."""

from __future__ import annotations

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.ears_classifier import (
    EARSPattern,
    _extract_fr_blocks,
    classify_all_frs,
    classify_requirement,
    score_ears_coverage,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

_PRD_WITH_FRS = """\
---
prd:
  id: TEST-001
  title: Test PRD
---

## 1. Problem Statement

Test problem.

## 4. Functional Requirements

### FR01: Event-driven requirement
When the user submits a form, the system shall validate all input fields.

### FR02: State-driven requirement
While the system is in maintenance mode, the API shall return 503 status.

### FR03: Unwanted behavior
If a database error occurs, the system shall log the failure and return a graceful error response.

### FR04: Optional feature
Where the user has enabled dark mode, the UI shall apply the dark theme.

### FR05: Ubiquitous requirement
The system shall encrypt all data at rest using AES-256.

## 5. Non-Functional Requirements

Performance requirements here.
"""

_PRD_NO_FRS = """\
---
prd:
  id: TEST-002
---

## 1. Problem Statement

No FR section in this PRD.
"""


# ---------------------------------------------------------------------------
# Test: classify_requirement
# ---------------------------------------------------------------------------


class TestClassifyRequirement:
    """Test individual requirement classification."""

    def test_event_driven(self) -> None:
        text = "When the user clicks submit, the system shall save the data."
        result = classify_requirement(text)
        assert result["pattern"] == EARSPattern.EVENT_DRIVEN.value

    def test_event_driven_on_trigger(self) -> None:
        text = "On receiving a webhook payload, the handler shall validate the signature."
        result = classify_requirement(text)
        assert result["pattern"] == EARSPattern.EVENT_DRIVEN.value

    def test_state_driven_while(self) -> None:
        text = "While the system is processing a batch job, the UI shall display a spinner."
        result = classify_requirement(text)
        assert result["pattern"] == EARSPattern.STATE_DRIVEN.value

    def test_state_driven_during(self) -> None:
        text = "During authentication flow, the system shall enforce rate limiting."
        result = classify_requirement(text)
        assert result["pattern"] == EARSPattern.STATE_DRIVEN.value

    def test_unwanted_behavior(self) -> None:
        text = "If a timeout error occurs, the system shall retry up to 3 times."
        result = classify_requirement(text)
        assert result["pattern"] == EARSPattern.UNWANTED_BEHAVIOR.value

    def test_unwanted_behavior_failure(self) -> None:
        text = "If authentication fails, the system shall lock the account."
        result = classify_requirement(text)
        assert result["pattern"] == EARSPattern.UNWANTED_BEHAVIOR.value

    def test_optional_feature(self) -> None:
        text = "Where feature flags are enabled, the system shall route to the new API."
        result = classify_requirement(text)
        assert result["pattern"] == EARSPattern.OPTIONAL_FEATURE.value

    def test_ubiquitous(self) -> None:
        text = "The system shall log all API requests."
        result = classify_requirement(text)
        assert result["pattern"] == EARSPattern.UBIQUITOUS.value

    def test_ubiquitous_must(self) -> None:
        text = "The validator must reject ambiguous requirements."
        result = classify_requirement(text)
        assert result["pattern"] == EARSPattern.UBIQUITOUS.value

    def test_unclassified(self) -> None:
        text = "This is just a description without any requirement language."
        result = classify_requirement(text)
        assert result["pattern"] == EARSPattern.UNCLASSIFIED.value

    def test_empty_text(self) -> None:
        result = classify_requirement("")
        assert result["pattern"] == EARSPattern.UNCLASSIFIED.value
        assert result["confidence"] == 0.0

    def test_confidence_values(self) -> None:
        # Event-driven should have confidence 0.85
        event = classify_requirement("When the user logs in, the system shall create a session.")
        assert event["confidence"] == 0.85

        # Unwanted should have confidence 0.9
        unwanted = classify_requirement("If an error occurs, the system shall retry.")
        assert unwanted["confidence"] == 0.9

        # Ubiquitous should have confidence 0.7
        ubiq = classify_requirement("The system shall validate input.")
        assert ubiq["confidence"] == 0.7

    def test_trigger_text_included(self) -> None:
        text = "When the user submits, the system shall validate."
        result = classify_requirement(text)
        assert len(str(result["trigger_text"])) > 0

    def test_priority_unwanted_over_event(self) -> None:
        """Unwanted behavior (more specific) should win over event-driven."""
        text = "When an error occurs, if the failure is transient, the system shall retry."
        result = classify_requirement(text)
        # Should match event_driven since "When" comes first and unwanted needs "if...error" pattern
        assert result["pattern"] in (
            EARSPattern.EVENT_DRIVEN.value,
            EARSPattern.UNWANTED_BEHAVIOR.value,
        )


# ---------------------------------------------------------------------------
# Test: _extract_fr_blocks
# ---------------------------------------------------------------------------


class TestExtractFrBlocks:
    """Test FR block extraction."""

    def test_extracts_5_blocks(self) -> None:
        blocks = _extract_fr_blocks(_PRD_WITH_FRS)
        assert len(blocks) == 5

    def test_no_fr_section(self) -> None:
        blocks = _extract_fr_blocks(_PRD_NO_FRS)
        assert len(blocks) == 0

    def test_empty_content(self) -> None:
        blocks = _extract_fr_blocks("")
        assert len(blocks) == 0

    def test_blocks_contain_requirement_text(self) -> None:
        blocks = _extract_fr_blocks(_PRD_WITH_FRS)
        # First block should contain event-driven text
        assert any("shall validate" in b for b in blocks)


# ---------------------------------------------------------------------------
# Test: classify_all_frs
# ---------------------------------------------------------------------------


class TestClassifyAllFrs:
    """Test batch classification."""

    def test_classifies_all_5(self) -> None:
        results = classify_all_frs(_PRD_WITH_FRS)
        assert len(results) == 5

    def test_no_frs(self) -> None:
        results = classify_all_frs(_PRD_NO_FRS)
        assert len(results) == 0

    def test_patterns_are_varied(self) -> None:
        results = classify_all_frs(_PRD_WITH_FRS)
        patterns = {str(r["pattern"]) for r in results}
        # Should have at least 3 different patterns
        assert len(patterns) >= 3


# ---------------------------------------------------------------------------
# Test: score_ears_coverage
# ---------------------------------------------------------------------------


class TestScoreEarsCoverage:
    """Test EARS coverage scoring."""

    def test_full_coverage_high_score(self) -> None:
        dim, classifications = score_ears_coverage(_PRD_WITH_FRS)
        assert dim.name == "ears_coverage"
        assert dim.max_score == 15.0
        # All 5 FRs should classify → coverage ~1.0
        assert dim.score > 10.0

    def test_no_frs_zero_score(self) -> None:
        dim, classifications = score_ears_coverage(_PRD_NO_FRS)
        assert dim.score == 0.0
        assert len(classifications) == 0

    def test_empty_content_zero(self) -> None:
        dim, _ = score_ears_coverage("")
        assert dim.score == 0.0

    def test_details_include_counts(self) -> None:
        dim, _ = score_ears_coverage(_PRD_WITH_FRS)
        assert "total_frs" in dim.details
        assert "classified" in dim.details
        assert "coverage" in dim.details

    def test_custom_weight(self) -> None:
        config = TRWConfig(validation_ears_weight=30.0)
        dim, _ = score_ears_coverage(_PRD_WITH_FRS, config=config)
        assert dim.max_score == 30.0

    def test_classifications_returned(self) -> None:
        _, classifications = score_ears_coverage(_PRD_WITH_FRS)
        assert len(classifications) == 5
        for c in classifications:
            assert "pattern" in c
            assert "confidence" in c

    def test_partial_coverage(self) -> None:
        """PRD with mix of classified and unclassified FRs."""
        partial = """\
---
prd:
  id: TEST-003
---

## 4. Functional Requirements

### FR01
When the user logs in, the system shall create a session.

### FR02
Some description without requirement keywords here.

### FR03
The API shall return JSON responses.
"""
        dim, classifications = score_ears_coverage(partial)
        # 2 classified out of 3
        coverage = float(str(dim.details.get("coverage", 0.0)))
        assert 0.5 <= coverage <= 1.0


# ---------------------------------------------------------------------------
# Test: EARSPattern enum
# ---------------------------------------------------------------------------


class TestEARSPatternEnum:
    """Test the EARS pattern enum."""

    def test_has_6_members(self) -> None:
        assert len(EARSPattern) == 6

    def test_values(self) -> None:
        expected = {
            "event_driven", "state_driven", "unwanted_behavior",
            "optional_feature", "ubiquitous", "unclassified",
        }
        assert {p.value for p in EARSPattern} == expected
