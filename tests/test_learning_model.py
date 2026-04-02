"""Tests for PRD-CORE-110 LearningEntry new fields.

Covers:
- test_new_fields_defaults: LearningEntry default values for new fields
- test_enum_validation: LearningEntry accepts valid type values
- test_learning_params_new_fields: LearningParams has new fields with correct defaults
"""

from __future__ import annotations

from trw_mcp.models.learning import LearningEntry
from trw_mcp.tools._learning_helpers import LearningParams


class TestLearningEntryNewFieldDefaults:
    """Test that new PRD-CORE-110 fields have correct defaults."""

    def test_new_fields_defaults(self) -> None:
        """LearningEntry defaults for new meta-learning fields match spec."""
        entry = LearningEntry(id="L-test", summary="test summary", detail="test detail")
        assert entry.type == "pattern"
        assert entry.nudge_line == ""
        assert entry.expires == ""
        assert entry.confidence == "unverified"
        assert entry.task_type == ""
        assert entry.domain == []
        assert entry.phase_origin == ""
        assert entry.phase_affinity == []
        assert entry.team_origin == ""
        assert entry.protection_tier == "normal"

    def test_type_default_is_pattern(self) -> None:
        """type defaults to 'pattern'."""
        entry = LearningEntry(id="L-x", summary="s", detail="d")
        assert entry.type == "pattern"

    def test_confidence_default_is_unverified(self) -> None:
        """confidence defaults to 'unverified'."""
        entry = LearningEntry(id="L-x", summary="s", detail="d")
        assert entry.confidence == "unverified"

    def test_protection_tier_default_is_normal(self) -> None:
        """protection_tier defaults to 'normal'."""
        entry = LearningEntry(id="L-x", summary="s", detail="d")
        assert entry.protection_tier == "normal"

    def test_domain_default_is_empty_list(self) -> None:
        """domain defaults to empty list."""
        entry = LearningEntry(id="L-x", summary="s", detail="d")
        assert entry.domain == []
        assert isinstance(entry.domain, list)

    def test_phase_affinity_default_is_empty_list(self) -> None:
        """phase_affinity defaults to empty list."""
        entry = LearningEntry(id="L-x", summary="s", detail="d")
        assert entry.phase_affinity == []
        assert isinstance(entry.phase_affinity, list)


class TestLearningEntryEnumValidation:
    """Test that LearningEntry accepts valid values for new enum-like fields."""

    def test_type_incident(self) -> None:
        entry = LearningEntry(id="L-1", summary="s", detail="d", type="incident")
        assert entry.type == "incident"

    def test_type_pattern(self) -> None:
        entry = LearningEntry(id="L-1", summary="s", detail="d", type="pattern")
        assert entry.type == "pattern"

    def test_type_convention(self) -> None:
        entry = LearningEntry(id="L-1", summary="s", detail="d", type="convention")
        assert entry.type == "convention"

    def test_type_hypothesis(self) -> None:
        entry = LearningEntry(id="L-1", summary="s", detail="d", type="hypothesis")
        assert entry.type == "hypothesis"

    def test_type_workaround(self) -> None:
        entry = LearningEntry(id="L-1", summary="s", detail="d", type="workaround")
        assert entry.type == "workaround"

    def test_confidence_values(self) -> None:
        for val in ("unverified", "low", "medium", "high", "verified"):
            entry = LearningEntry(id="L-1", summary="s", detail="d", confidence=val)
            assert entry.confidence == val

    def test_protection_tier_values(self) -> None:
        for val in ("normal", "protected", "permanent"):
            entry = LearningEntry(id="L-1", summary="s", detail="d", protection_tier=val)
            assert entry.protection_tier == val

    def test_phase_origin_values(self) -> None:
        for val in ("RESEARCH", "PLAN", "IMPLEMENT", "VALIDATE", "REVIEW", "DELIVER"):
            entry = LearningEntry(id="L-1", summary="s", detail="d", phase_origin=val)
            assert entry.phase_origin == val

    def test_nudge_line_custom(self) -> None:
        entry = LearningEntry(id="L-1", summary="s", detail="d", nudge_line="Use X not Y")
        assert entry.nudge_line == "Use X not Y"

    def test_domain_list(self) -> None:
        entry = LearningEntry(id="L-1", summary="s", detail="d", domain=["testing", "mcp"])
        assert entry.domain == ["testing", "mcp"]

    def test_phase_affinity_list(self) -> None:
        entry = LearningEntry(id="L-1", summary="s", detail="d", phase_affinity=["IMPLEMENT", "VALIDATE"])
        assert entry.phase_affinity == ["IMPLEMENT", "VALIDATE"]

    def test_team_origin_custom(self) -> None:
        entry = LearningEntry(id="L-1", summary="s", detail="d", team_origin="trw-sprint-team")
        assert entry.team_origin == "trw-sprint-team"

    def test_task_type_custom(self) -> None:
        entry = LearningEntry(id="L-1", summary="s", detail="d", task_type="debugging")
        assert entry.task_type == "debugging"

    def test_expires_iso_date(self) -> None:
        entry = LearningEntry(id="L-1", summary="s", detail="d", expires="2026-12-31")
        assert entry.expires == "2026-12-31"


class TestLearningParamsNewFields:
    """Test that LearningParams has new PRD-CORE-110 fields."""

    def test_default_values(self) -> None:
        """LearningParams new fields have correct defaults."""
        params = LearningParams(
            summary="s",
            detail="d",
            learning_id="L-x",
            tags=[],
            evidence=[],
            impact=0.5,
            source_type="agent",
            source_identity="",
        )
        assert params.type == "pattern"
        assert params.nudge_line == ""
        assert params.expires == ""
        assert params.confidence == "unverified"
        assert params.task_type == ""
        assert params.domain is None
        assert params.phase_origin == ""
        assert params.phase_affinity is None
        assert params.team_origin == ""
        assert params.protection_tier == "normal"

    def test_custom_values(self) -> None:
        """LearningParams accepts custom values for new fields."""
        params = LearningParams(
            summary="s",
            detail="d",
            learning_id="L-x",
            tags=[],
            evidence=[],
            impact=0.7,
            source_type="agent",
            source_identity="",
            type="incident",
            nudge_line="Short recall nudge",
            confidence="high",
            protection_tier="protected",
            phase_origin="IMPLEMENT",
            domain=["testing"],
            phase_affinity=["VALIDATE"],
            team_origin="sprint-80",
            task_type="debugging",
        )
        assert params.type == "incident"
        assert params.nudge_line == "Short recall nudge"
        assert params.confidence == "high"
        assert params.protection_tier == "protected"
        assert params.phase_origin == "IMPLEMENT"
        assert params.domain == ["testing"]
        assert params.phase_affinity == ["VALIDATE"]
        assert params.team_origin == "sprint-80"
        assert params.task_type == "debugging"
