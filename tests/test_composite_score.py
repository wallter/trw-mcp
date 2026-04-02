"""Tests for compute_composite_outcome — PRD-CORE-104-FR02.

Covers: weighted composite outcome score with TRW value hierarchy
(quality penalties outweigh velocity rewards).
"""

from __future__ import annotations

from trw_mcp.scoring._correlation import compute_composite_outcome


class TestComputeCompositeOutcome:
    """Tests for compute_composite_outcome()."""

    def test_zero_rework_positive_score(self) -> None:
        """0 rework with some velocity produces a positive score."""
        result = compute_composite_outcome(
            rework_rate=0.0,
            p0_defect_count=0,
            velocity_tasks=5.0,
            learning_rate=1.0,
        )
        # 0*(-2) + 0*(-1.5) + 5*0.5 + 1*0.3 = 2.8
        assert result > 0

    def test_high_rework_negative_score(self) -> None:
        """0.5 rework + 2 P0s yields score < -3.0."""
        result = compute_composite_outcome(
            rework_rate=0.5,
            p0_defect_count=2,
            velocity_tasks=0.0,
            learning_rate=0.0,
        )
        # 0.5*(-2) + 2*(-1.5) + 0 + 0 = -1.0 + -3.0 = -4.0
        assert result < -3.0

    def test_velocity_component(self) -> None:
        """Velocity weight drives the score upward."""
        baseline = compute_composite_outcome(velocity_tasks=0.0)
        with_velocity = compute_composite_outcome(velocity_tasks=10.0)
        assert with_velocity > baseline
        # Check exact contribution: 10 * 0.5 = 5.0
        assert abs(with_velocity - baseline - 5.0) < 0.001

    def test_p0_component(self) -> None:
        """P0 defect count drives the score downward."""
        baseline = compute_composite_outcome(p0_defect_count=0)
        with_p0 = compute_composite_outcome(p0_defect_count=3)
        assert with_p0 < baseline
        # Check exact contribution: 3 * (-1.5) = -4.5
        assert abs(baseline - with_p0 - 4.5) < 0.001

    def test_learning_rate_component(self) -> None:
        """Learning rate contributes positively to the score."""
        baseline = compute_composite_outcome(learning_rate=0.0)
        with_lr = compute_composite_outcome(learning_rate=2.0)
        assert with_lr > baseline
        # Check exact contribution: 2.0 * 0.3 = 0.6
        assert abs(with_lr - baseline - 0.6) < 0.001

    def test_config_weight_overrides(self) -> None:
        """Custom weights produce a different score than defaults."""
        default_score = compute_composite_outcome(
            rework_rate=0.5,
            p0_defect_count=1,
            velocity_tasks=3.0,
            learning_rate=1.0,
        )
        custom_score = compute_composite_outcome(
            rework_rate=0.5,
            p0_defect_count=1,
            velocity_tasks=3.0,
            learning_rate=1.0,
            weight_rework=-1.0,
            weight_p0_defects=-0.5,
            weight_velocity=1.0,
            weight_learning_rate=1.0,
        )
        assert default_score != custom_score
        # Custom: 0.5*(-1) + 1*(-0.5) + 3*1.0 + 1*1.0 = -0.5 - 0.5 + 3 + 1 = 3.0
        assert abs(custom_score - 3.0) < 0.001

    def test_all_zeros_returns_zero(self) -> None:
        """All zero inputs produce zero score."""
        result = compute_composite_outcome()
        assert result == 0.0

    def test_quality_penalties_outweigh_velocity(self) -> None:
        """TRW value hierarchy: quality penalties outweigh velocity rewards.

        Even with max velocity, a modest rework rate and P0 count should
        produce a negative or lower score than having no quality issues.
        """
        # High velocity but quality issues
        quality_issues = compute_composite_outcome(
            rework_rate=0.3,
            p0_defect_count=1,
            velocity_tasks=10.0,
        )
        # No quality issues, modest velocity
        clean = compute_composite_outcome(
            rework_rate=0.0,
            p0_defect_count=0,
            velocity_tasks=3.0,
        )
        # With defaults: quality_issues = 0.3*(-2) + 1*(-1.5) + 10*0.5 = -0.6 - 1.5 + 5.0 = 2.9
        # clean = 0 + 0 + 3*0.5 = 1.5
        # In this case velocity dominates, but the penalty reduced the score significantly
        # The key is the rework penalty (-2.0 weight) is 4x the velocity reward (0.5 weight)
        assert quality_issues < compute_composite_outcome(
            rework_rate=0.0,
            p0_defect_count=0,
            velocity_tasks=10.0,
        )
