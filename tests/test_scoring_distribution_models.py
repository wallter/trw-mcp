"""Tests for scoring impact distribution and complexity models."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from trw_mcp.models.run import (
    ComplexityClass,
    ComplexityOverride,
    ComplexitySignals,
    PhaseRequirements,
    RunState,
)
from trw_mcp.scoring import compute_impact_distribution


class TestComputeImpactDistribution:
    """Tests for compute_impact_distribution function."""

    def _write_entry(self, entries_dir: Path, fname: str, impact: float, status: str = "active") -> None:
        entries_dir.mkdir(parents=True, exist_ok=True)
        (entries_dir / fname).write_text(f"id: {fname}\nimpact: {impact}\nstatus: {status}\n")

    def test_empty_dir_returns_zeros(self, tmp_path: Path) -> None:
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        result = compute_impact_distribution(entries_dir)
        assert result["total_active"] == 0
        critical = result["critical"]
        assert isinstance(critical, dict)
        assert critical["count"] == 0
        assert critical["pct"] == 0.0

    def test_nonexistent_dir_returns_zeros(self, tmp_path: Path) -> None:
        result = compute_impact_distribution(tmp_path / "nonexistent")
        assert result["total_active"] == 0

    def test_mixed_tiers(self, tmp_path: Path) -> None:
        entries_dir = tmp_path / "entries"
        # 1 critical (0.95), 2 high (0.75, 0.80), 1 medium (0.5), 1 low (0.2)
        self._write_entry(entries_dir, "a.yaml", 0.95)
        self._write_entry(entries_dir, "b.yaml", 0.75)
        self._write_entry(entries_dir, "c.yaml", 0.80)
        self._write_entry(entries_dir, "d.yaml", 0.50)
        self._write_entry(entries_dir, "e.yaml", 0.20)
        result = compute_impact_distribution(entries_dir)
        assert result["total_active"] == 5
        critical = result["critical"]
        assert isinstance(critical, dict)
        assert critical["count"] == 1
        assert abs(critical["pct"] - 0.2) < 0.01
        high = result["high"]
        assert isinstance(high, dict)
        assert high["count"] == 2
        assert abs(high["pct"] - 0.4) < 0.01
        medium = result["medium"]
        assert isinstance(medium, dict)
        assert medium["count"] == 1
        low = result["low"]
        assert isinstance(low, dict)
        assert low["count"] == 1

    def test_ignores_inactive_entries(self, tmp_path: Path) -> None:
        entries_dir = tmp_path / "entries"
        self._write_entry(entries_dir, "active.yaml", 0.9)
        self._write_entry(entries_dir, "resolved.yaml", 0.9, status="resolved")
        self._write_entry(entries_dir, "obsolete.yaml", 0.9, status="obsolete")
        result = compute_impact_distribution(entries_dir)
        assert result["total_active"] == 1
        critical = result["critical"]
        assert isinstance(critical, dict)
        assert critical["count"] == 1


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
            signals.files_affected = 5  # type: ignore[misc]


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
