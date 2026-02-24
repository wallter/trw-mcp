"""Tests for scoring module — compute_utility_score and update_q_value."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.scoring import compute_impact_distribution, compute_utility_score, update_q_value


class TestUpdateQValue:
    """Tests for the MemRL Q-value update formula."""

    def test_positive_reward_increases_q(self) -> None:
        assert update_q_value(0.5, 1.0) > 0.5

    def test_negative_reward_decreases_q(self) -> None:
        assert update_q_value(0.5, -0.5) < 0.5

    def test_zero_reward_moves_toward_zero(self) -> None:
        assert update_q_value(0.5, 0.0) < 0.5

    def test_same_reward_is_stable(self) -> None:
        """When reward == q_old, Q stays the same (no bonus)."""
        assert update_q_value(0.5, 0.5) == pytest.approx(0.5)

    def test_convergence_to_true_value(self) -> None:
        """After many updates with constant reward, Q converges."""
        q = 0.5
        for _ in range(50):
            q = update_q_value(q, 0.8)
        assert abs(q - 0.8) < 0.01

    def test_convergence_to_low_value(self) -> None:
        """Converges downward as well."""
        q = 0.8
        for _ in range(50):
            q = update_q_value(q, 0.2)
        assert abs(q - 0.2) < 0.01

    def test_clamped_upper_bound(self) -> None:
        assert update_q_value(0.99, 1.0) <= 1.0
        assert update_q_value(0.99, 1.0, recurrence_bonus=0.1) <= 1.0

    def test_clamped_lower_bound(self) -> None:
        assert update_q_value(0.01, -1.0) >= 0.0

    def test_recurrence_bonus_increases_q(self) -> None:
        q_without = update_q_value(0.5, 0.8, recurrence_bonus=0.0)
        q_with = update_q_value(0.5, 0.8, recurrence_bonus=0.02)
        assert q_with > q_without

    def test_custom_alpha(self) -> None:
        """Higher alpha means faster adaptation."""
        q_slow = update_q_value(0.5, 1.0, alpha=0.05)
        q_fast = update_q_value(0.5, 1.0, alpha=0.5)
        assert q_fast > q_slow

    def test_half_life_of_adaptation(self) -> None:
        """After ~4.3 updates at alpha=0.15, should be within 50% of target."""
        q = 0.0
        target = 1.0
        for _ in range(5):
            q = update_q_value(q, target)
        # Should be at least 50% of the way there
        assert q > 0.5 * target


class TestComputeUtilityScore:
    """Tests for the composite utility scoring function."""

    def test_fresh_high_value(self) -> None:
        """Fresh, high-Q learning has high utility."""
        score = compute_utility_score(0.9, 0, 1, 0.9, 5)
        assert score > 0.85

    def test_fresh_default_value(self) -> None:
        """Fresh, default learning has ~0.5 utility."""
        score = compute_utility_score(0.5, 0, 1, 0.5, 5)
        assert 0.45 < score < 0.55

    def test_decay_without_access(self) -> None:
        """Utility decays over time without access."""
        score_fresh = compute_utility_score(0.5, 0, 1, 0.5, 5)
        score_2wk = compute_utility_score(0.5, 14, 1, 0.5, 5)
        score_1mo = compute_utility_score(0.5, 30, 1, 0.5, 5)
        assert score_2wk < score_fresh
        assert score_1mo < score_2wk

    def test_two_month_unused_low_utility(self) -> None:
        """Two months unused drops below prune threshold."""
        score = compute_utility_score(0.5, 60, 1, 0.5, 5)
        assert score < 0.10

    def test_recurrence_slows_decay(self) -> None:
        """Higher recurrence extends effective half-life."""
        score_low = compute_utility_score(0.5, 14, 1, 0.5, 5)
        score_high = compute_utility_score(0.5, 14, 10, 0.5, 5)
        assert score_high > score_low

    def test_high_q_frequently_recalled(self) -> None:
        """High Q + frequent recalls persists strongly."""
        score = compute_utility_score(0.9, 7, 10, 0.9, 5)
        assert score > 0.75

    def test_cold_start_uses_impact(self) -> None:
        """With 0 observations, utility is based on base_impact."""
        score = compute_utility_score(0.3, 0, 1, 0.7, 0)
        # q_value=0.3 ignored, base_impact=0.7 used
        assert abs(score - 0.7) < 0.01

    def test_cold_start_partial_blend(self) -> None:
        """With 1 observation (threshold=3), blend is 2/3 impact + 1/3 q."""
        score = compute_utility_score(0.3, 0, 1, 0.9, 1)
        # effective_q = (1 - 1/3) * 0.9 + (1/3) * 0.3 = 0.6 + 0.1 = 0.7
        assert abs(score - 0.7) < 0.01

    def test_cold_start_fully_converged(self) -> None:
        """With >= threshold observations, q_value is fully trusted."""
        score = compute_utility_score(0.3, 0, 1, 0.9, 5)
        # effective_q = 0.3 (q_value)
        assert abs(score - 0.3) < 0.01

    def test_output_clamped_to_unit_range(self) -> None:
        """Score always in [0.0, 1.0]."""
        assert compute_utility_score(1.0, 0, 100, 1.0, 100) <= 1.0
        assert compute_utility_score(0.0, 1000, 1, 0.0, 0) >= 0.0

    def test_zero_days_no_decay(self) -> None:
        """Zero days since access means no decay applied."""
        score = compute_utility_score(0.8, 0, 1, 0.8, 5)
        assert abs(score - 0.8) < 0.01

    def test_negative_days_treated_as_zero(self) -> None:
        """Negative days_since_last_access treated as 0 (no future decay)."""
        score = compute_utility_score(0.8, -5, 1, 0.8, 5)
        assert abs(score - 0.8) < 0.01

    def test_custom_half_life(self) -> None:
        """Shorter half-life causes faster decay."""
        score_short = compute_utility_score(
            0.5, 7, 1, 0.5, 5, half_life_days=7.0,
        )
        score_long = compute_utility_score(
            0.5, 7, 1, 0.5, 5, half_life_days=28.0,
        )
        assert score_short < score_long

    def test_half_life_exact(self) -> None:
        """At exactly half_life_days, retention is ~50% (for recurrence=1)."""
        score = compute_utility_score(1.0, 14, 1, 1.0, 5, half_life_days=14.0)
        assert abs(score - 0.5) < 0.01

    def test_custom_use_exponent(self) -> None:
        """Higher use_exponent amplifies recurrence benefit."""
        score_low = compute_utility_score(
            0.5, 14, 5, 0.5, 5, use_exponent=0.3,
        )
        score_high = compute_utility_score(
            0.5, 14, 5, 0.5, 5, use_exponent=0.9,
        )
        assert score_high > score_low

    def test_monotonic_decay(self) -> None:
        """Utility is monotonically decreasing with days (all else equal)."""
        scores = [
            compute_utility_score(0.5, d, 1, 0.5, 5)
            for d in range(0, 60, 5)
        ]
        for i in range(1, len(scores)):
            assert scores[i] <= scores[i - 1]


class TestComputeImpactDistribution:
    """Tests for compute_impact_distribution function."""

    def _write_entry(self, entries_dir: Path, fname: str, impact: float, status: str = "active") -> None:
        entries_dir.mkdir(parents=True, exist_ok=True)
        (entries_dir / fname).write_text(
            f"id: {fname}\nimpact: {impact}\nstatus: {status}\n"
        )

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
