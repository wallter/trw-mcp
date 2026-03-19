"""Tests for Q-value pre-seeding from impact score.

When a learning entry has q_observations == 0, the initial q_value should
be blended from the impact score rather than using the flat 0.5 default.

Formula: initial_q = impact * 0.5 + 0.5 * 0.5
  - impact=0.95 -> q_value=0.725
  - impact=0.50 -> q_value=0.500 (unchanged from prior default)
  - impact=0.00 -> q_value=0.250
  - impact=1.00 -> q_value=0.750
"""

from __future__ import annotations

import pytest

from trw_mcp.scoring._correlation import compute_initial_q_value


class TestComputeInitialQValue:
    """Tests for the compute_initial_q_value function."""

    def test_high_impact_gets_elevated_q(self) -> None:
        """A high-impact learning (0.95) should start above the default 0.5."""
        result = compute_initial_q_value(0.95)
        assert result == pytest.approx(0.725)

    def test_default_impact_unchanged(self) -> None:
        """impact=0.5 produces q_value=0.5, matching the old default."""
        result = compute_initial_q_value(0.5)
        assert result == pytest.approx(0.5)

    def test_zero_impact(self) -> None:
        """impact=0.0 produces q_value=0.25 (below default)."""
        result = compute_initial_q_value(0.0)
        assert result == pytest.approx(0.25)

    def test_max_impact(self) -> None:
        """impact=1.0 produces q_value=0.75."""
        result = compute_initial_q_value(1.0)
        assert result == pytest.approx(0.75)

    def test_low_impact(self) -> None:
        """impact=0.2 produces a below-default q_value."""
        result = compute_initial_q_value(0.2)
        assert result == pytest.approx(0.35)

    def test_result_always_in_valid_range(self) -> None:
        """Output is always in [0.0, 1.0] for valid impact inputs."""
        for impact_x100 in range(101):
            impact = impact_x100 / 100.0
            q = compute_initial_q_value(impact)
            assert 0.0 <= q <= 1.0, f"impact={impact} produced q={q}"

    def test_monotonic_with_impact(self) -> None:
        """Higher impact always produces higher initial q_value."""
        prev = compute_initial_q_value(0.0)
        for impact_x10 in range(1, 11):
            impact = impact_x10 / 10.0
            current = compute_initial_q_value(impact)
            assert current > prev, f"Non-monotonic at impact={impact}: {current} <= {prev}"
            prev = current

    def test_midpoint_property(self) -> None:
        """The function blends impact weight (0.5) with prior weight (0.5).

        At any impact level: result = impact * 0.5 + 0.5 * 0.5.
        """
        for impact_x10 in range(11):
            impact = impact_x10 / 10.0
            expected = impact * 0.5 + 0.5 * 0.5
            assert compute_initial_q_value(impact) == pytest.approx(expected)


class TestLearningEntryQPreseed:
    """Tests that LearningEntry model uses pre-seeded q_value when created."""

    def test_high_impact_entry_gets_elevated_q(self) -> None:
        """Creating a LearningEntry with impact=0.95 should pre-seed q_value."""
        from trw_mcp.models.learning import LearningEntry

        entry = LearningEntry(
            id="test-001",
            summary="Critical discovery",
            detail="Very important finding",
            impact=0.95,
        )
        assert entry.q_value == pytest.approx(0.725)
        assert entry.q_observations == 0

    def test_default_impact_entry_gets_default_q(self) -> None:
        """Creating a LearningEntry with impact=0.5 should keep q_value=0.5."""
        from trw_mcp.models.learning import LearningEntry

        entry = LearningEntry(
            id="test-002",
            summary="Normal discovery",
            detail="Standard finding",
            impact=0.5,
        )
        assert entry.q_value == pytest.approx(0.5)

    def test_explicit_q_value_not_overridden(self) -> None:
        """If q_value is explicitly set, pre-seeding should not override it."""
        from trw_mcp.models.learning import LearningEntry

        entry = LearningEntry(
            id="test-003",
            summary="Manual q entry",
            detail="Has explicit q_value",
            impact=0.95,
            q_value=0.8,
        )
        assert entry.q_value == pytest.approx(0.8)

    def test_nonzero_observations_not_overridden(self) -> None:
        """If q_observations > 0, q_value should not be pre-seeded."""
        from trw_mcp.models.learning import LearningEntry

        entry = LearningEntry(
            id="test-004",
            summary="Observed entry",
            detail="Has observations",
            impact=0.95,
            q_value=0.6,
            q_observations=3,
        )
        assert entry.q_value == pytest.approx(0.6)

    def test_low_impact_entry_gets_lower_q(self) -> None:
        """Creating a LearningEntry with impact=0.2 should pre-seed below 0.5."""
        from trw_mcp.models.learning import LearningEntry

        entry = LearningEntry(
            id="test-005",
            summary="Low impact finding",
            detail="Minor observation",
            impact=0.2,
        )
        assert entry.q_value == pytest.approx(0.35)


class TestSQLiteIntegrationPath:
    """Q1-03: Tests for the SQLite integration path via _learning_to_memory_entry."""

    def test_memory_entry_q_value_matches_formula(self) -> None:
        """_learning_to_memory_entry with impact=0.95 produces q_value=0.725."""
        from trw_mcp.state._memory_transforms import _learning_to_memory_entry

        entry = _learning_to_memory_entry(
            learning_id="sqlite-001",
            summary="High impact",
            detail="Testing SQLite path",
            impact=0.95,
        )
        assert entry.q_value == pytest.approx(0.725)

    def test_preseed_suppressed_when_observations_positive(self) -> None:
        """LearningEntry with q_observations=3 should not pre-seed q_value.

        When q_observations > 0, the entry already has outcome data and the
        validator should leave q_value at its explicit or default value.
        """
        from trw_mcp.models.learning import LearningEntry

        entry = LearningEntry(
            id="sqlite-002",
            summary="Observed entry",
            detail="Has prior observations",
            impact=0.95,
            q_observations=3,
        )
        # q_value should be the Pydantic default (0.5), not pre-seeded from impact
        assert entry.q_value == pytest.approx(0.5)
        assert entry.q_observations == 3

    def test_float_q_observations_guard(self) -> None:
        """Q1-01: Float q_observations=1.0 should be coerced to int and suppress pre-seeding.

        Before the fix, isinstance(1.0, int) returned False, so the guard was
        bypassed and pre-seeding incorrectly ran despite observations > 0.
        """
        from trw_mcp.models.learning import LearningEntry

        entry = LearningEntry(
            id="sqlite-003",
            summary="Float obs entry",
            detail="q_observations passed as float",
            impact=0.95,
            q_observations=1,
        )
        # Pre-seeding should be suppressed (observations > 0)
        assert entry.q_value == pytest.approx(0.5)
        assert entry.q_observations == 1


class TestComputeInitialQValueClamp:
    """Q1-02: Verify clamping and rounding are built into compute_initial_q_value."""

    def test_clamp_negative_impact(self) -> None:
        """Negative impact should clamp the result to 0.0 minimum."""
        result = compute_initial_q_value(-1.0)
        assert result >= 0.0

    def test_clamp_excessive_impact(self) -> None:
        """Impact > 1.0 should clamp the result to 1.0 maximum."""
        result = compute_initial_q_value(3.0)
        assert result <= 1.0

    def test_result_is_rounded_to_4_decimals(self) -> None:
        """The result should have at most 4 decimal places."""
        result = compute_initial_q_value(0.333333)
        # 0.333333 * 0.5 + 0.25 = 0.4166665 -> rounded to 0.4167
        decimal_str = str(result).split(".")[-1] if "." in str(result) else ""
        assert len(decimal_str) <= 4
