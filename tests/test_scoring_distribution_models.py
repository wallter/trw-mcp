"""Tests for scoring impact distribution and complexity models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trw_mcp.models.run import (
    ComplexityClass,
    ComplexityOverride,
    ComplexitySignals,
    PhaseRequirements,
    RunState,
)


class TestComplexitySignals:
    """Tests for ComplexitySignals model (FR02)."""

    def test_defaults(self) -> None:
        signals = ComplexitySignals()
        assert signals.files_affected == 1
        assert signals.novel_patterns is False
        assert signals.cross_cutting is False
        assert signals.architecture_change is False
        assert signals.external_integration is False
        assert signals.large_refactoring is False
        assert signals.security_change is False
        assert signals.data_migration is False
        assert signals.unknown_codebase is False

    def test_files_affected_negative_raises(self) -> None:
        with pytest.raises(ValidationError):
            ComplexitySignals(files_affected=-1)

    def test_files_affected_capped_at_100(self) -> None:
        with pytest.raises(ValidationError):
            ComplexitySignals(files_affected=101)

    def test_frozen_model(self) -> None:
        signals = ComplexitySignals()
        with pytest.raises(ValidationError):
            signals.files_affected = 5


class TestComplexityOverride:
    """Tests for ComplexityOverride model (FR09)."""

    def test_basic_creation(self) -> None:
        override = ComplexityOverride(
            reason="hard override",
            signals=["security_change", "data_migration"],
            raw_score=2,
        )
        assert override.reason == "hard override"
        assert len(override.signals) == 2
        assert override.raw_score == 2


class TestRunStateComplexityFields:
    """Tests for RunState complexity fields (FR02, FR09)."""

    def test_runstate_defaults_none(self) -> None:
        rs = RunState(run_id="test-1", task="test")
        assert rs.complexity_class is None
        assert rs.complexity_signals is None
        assert rs.complexity_override is None
        assert rs.phase_requirements is None

    def test_runstate_with_complexity(self) -> None:
        rs = RunState(
            run_id="test-2",
            task="test",
            complexity_class=ComplexityClass.COMPREHENSIVE,
            complexity_signals=ComplexitySignals(
                files_affected=5,
                architecture_change=True,
            ),
        )
        assert rs.complexity_class == "COMPREHENSIVE"  # use_enum_values=True
        assert rs.complexity_signals is not None
        assert rs.complexity_signals.files_affected == 5

    def test_runstate_yaml_roundtrip(self) -> None:
        """Ensure enum values survive JSON/YAML serialization."""
        import json

        rs = RunState(
            run_id="rt-1",
            task="roundtrip",
            complexity_class=ComplexityClass.COMPREHENSIVE,
            complexity_override=ComplexityOverride(
                reason="test",
                signals=["security_change"],
                raw_score=3,
            ),
            phase_requirements=PhaseRequirements(
                mandatory=["IMPLEMENT", "DELIVER"],
                optional=[],
                skipped=["RESEARCH"],
            ),
        )
        data = json.loads(rs.model_dump_json())
        assert data["complexity_class"] == "COMPREHENSIVE"
        assert data["complexity_override"]["reason"] == "test"
        assert data["phase_requirements"]["mandatory"] == ["IMPLEMENT", "DELIVER"]

        # Deserialize back
        rs2 = RunState(**data)
        assert rs2.complexity_class == "COMPREHENSIVE"
        assert rs2.complexity_override is not None
        assert rs2.complexity_override.raw_score == 3
