"""Tests for scoring module Q-value and utility helpers."""

from __future__ import annotations

from trw_mcp.scoring import compute_utility_score


class TestComputeUtilityScore:
    """Tests for the composite utility scoring function."""

    def test_fresh_high_value(self) -> None:
        """Fresh, high-Q learning has high utility."""
        score = compute_utility_score(0, 1, 0.9)
        assert score > 0.85

    def test_fresh_default_value(self) -> None:
        """Fresh, default learning has ~0.5 utility."""
        score = compute_utility_score(0, 1, 0.5)
        assert 0.45 < score < 0.55

    def test_decay_without_access(self) -> None:
        """Utility decays over time without access."""
        score_fresh = compute_utility_score(0, 1, 0.5)
        score_2wk = compute_utility_score(14, 1, 0.5)
        score_1mo = compute_utility_score(30, 1, 0.5)
        assert score_2wk < score_fresh
        assert score_1mo < score_2wk

    def test_two_month_unused_low_utility(self) -> None:
        """Two months unused drops below prune threshold."""
        score = compute_utility_score(60, 1, 0.5)
        assert score < 0.10

    def test_recurrence_slows_decay(self) -> None:
        """Higher recurrence extends effective half-life."""
        score_low = compute_utility_score(14, 1, 0.5)
        score_high = compute_utility_score(14, 10, 0.5)
        assert score_high > score_low

    def test_high_q_frequently_recalled(self) -> None:
        """High Q + frequent recalls persists strongly."""
        score = compute_utility_score(7, 10, 0.9)
        assert score > 0.75

    def test_cold_start_uses_impact(self) -> None:
        """Utility is based on base_impact (PRD-CORE-293: no Q blend)."""
        score = compute_utility_score(0, 1, 0.7)
        assert abs(score - 0.7) < 0.01

    def test_output_clamped_to_unit_range(self) -> None:
        """Score always in [0.0, 1.0]."""
        assert compute_utility_score(0, 100, 1.0) <= 1.0
        assert compute_utility_score(1000, 1, 0.0) >= 0.0

    def test_zero_days_no_decay(self) -> None:
        """Zero days since access means no decay applied."""
        score = compute_utility_score(0, 1, 0.8)
        assert abs(score - 0.8) < 0.01

    def test_negative_days_treated_as_zero(self) -> None:
        """Negative days_since_last_access treated as 0 (no future decay)."""
        score = compute_utility_score(-5, 1, 0.8)
        assert abs(score - 0.8) < 0.01

    def test_custom_half_life(self) -> None:
        """Shorter half-life causes faster decay."""
        score_short = compute_utility_score(7, 1, 0.5, half_life_days=7.0)
        score_long = compute_utility_score(7, 1, 0.5, half_life_days=28.0)
        assert score_short < score_long

    def test_half_life_exact(self) -> None:
        """At exactly half_life_days, retention is ~50% (for recurrence=1)."""
        score = compute_utility_score(14, 1, 1.0, half_life_days=14.0)
        assert abs(score - 0.5) < 0.01

    def test_custom_use_exponent(self) -> None:
        """Higher use_exponent amplifies recurrence benefit."""
        score_low = compute_utility_score(14, 5, 0.5, use_exponent=0.3)
        score_high = compute_utility_score(14, 5, 0.5, use_exponent=0.9)
        assert score_high > score_low

    def test_monotonic_decay(self) -> None:
        """Utility is monotonically decreasing with days (all else equal)."""
        scores = [compute_utility_score(d, 1, 0.5) for d in range(0, 60, 5)]
        for i in range(1, len(scores)):
            assert scores[i] <= scores[i - 1]
