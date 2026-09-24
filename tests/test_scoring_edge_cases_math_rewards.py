"""Edge-case tests for scoring math and event reward resolution."""

from __future__ import annotations

from trw_mcp.scoring import compute_utility_score


class TestComputeUtilityScoreMath:
    """Mathematical correctness checks for compute_utility_score."""

    def test_access_count_boost(self) -> None:
        """access_count parameter provides a boost."""
        score_no_access = compute_utility_score(7, 1, 0.5, access_count=0)
        score_with_access = compute_utility_score(7, 1, 0.5, access_count=10)
        assert score_with_access >= score_no_access

    def test_source_human_boost(self) -> None:
        """source_type='human' provides a utility boost."""
        score_agent = compute_utility_score(7, 1, 0.5, source_type="agent")
        score_human = compute_utility_score(7, 1, 0.5, source_type="human")
        assert score_human >= score_agent

    def test_very_high_recurrence_caps_benefit(self) -> None:
        """Extremely high recurrence doesn't produce score > 1.0."""
        score = compute_utility_score(0, 10000, 1.0)
        assert score <= 1.0

    def test_zero_half_life_handled(self) -> None:
        """half_life_days=0 doesn't cause division by zero."""
        score = compute_utility_score(10, 1, 0.5, half_life_days=0.0)
        assert 0.0 <= score <= 1.0

    def test_access_count_boost_capped(self) -> None:
        """Access count boost has a cap (doesn't grow indefinitely)."""
        score_100 = compute_utility_score(7, 1, 0.5, access_count=100)
        score_10000 = compute_utility_score(7, 1, 0.5, access_count=10000)
        assert abs(score_10000 - score_100) < 0.05
