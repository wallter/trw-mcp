"""Tests for velocity pure functions (PRD-CORE-015).

Tests cover: compute_run_velocity, _compute_phase_durations,
compute_learning_effectiveness, compute_debt_indicators,
compute_overhead_ratio, linear_fit, sign_test, detect_confounders,
compute_trend.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.velocity import (
    _binomial_tail,
    _binom_pmf,
    _compute_phase_durations,
    _FLOAT_EPSILON,
    compute_debt_indicators,
    compute_learning_effectiveness,
    compute_overhead_ratio,
    compute_run_velocity,
    compute_trend,
    detect_confounders,
    linear_fit,
    sign_test,
)


# --- compute_run_velocity ---


class TestComputeRunVelocity:
    """Tests for compute_run_velocity."""

    def test_empty_events(self) -> None:
        result = compute_run_velocity([])
        assert result.total_duration_minutes == 0.0
        assert result.shard_throughput == 0.0

    def test_single_event(self) -> None:
        events = [{"ts": "2026-02-07T10:00:00Z", "event": "run_init"}]
        result = compute_run_velocity(events)
        assert result.total_duration_minutes == 0.0

    def test_duration_and_throughput(self) -> None:
        events = [
            {"ts": "2026-02-07T10:00:00Z", "event": "run_init"},
            {"ts": "2026-02-07T10:30:00Z", "event": "shard_complete"},
            {"ts": "2026-02-07T11:00:00Z", "event": "shard_complete"},
        ]
        result = compute_run_velocity(events)
        assert result.total_duration_minutes == 60.0
        assert result.shard_throughput == 2.0  # 2 shards / 1 hour

    def test_wave_manifest_completion_rate(self) -> None:
        events = [
            {"ts": "2026-02-07T10:00:00Z", "event": "run_init"},
            {"ts": "2026-02-07T11:00:00Z", "event": "done"},
        ]
        manifest: dict[str, object] = {
            "waves": [
                {"shards": ["s1", "s2"], "status": "complete"},
                {"shards": ["s3"], "status": "pending"},
            ]
        }
        result = compute_run_velocity(events, manifest)
        assert result.completion_rate == pytest.approx(2.0 / 3.0, abs=0.01)

    def test_waves_completed_count(self) -> None:
        events = [
            {"ts": "2026-02-07T10:00:00Z", "event": "run_init"},
            {"ts": "2026-02-07T10:30:00Z", "event": "wave_validated", "valid": True},
            {"ts": "2026-02-07T11:00:00Z", "event": "wave_validated", "valid": False},
        ]
        result = compute_run_velocity(events)
        assert result.waves_completed == 1

    def test_learning_reuse_count(self) -> None:
        events = [
            {"ts": "2026-02-07T10:00:00Z", "event": "run_init"},
            {"ts": "2026-02-07T10:05:00Z", "event": "recall_query"},
            {"ts": "2026-02-07T10:10:00Z", "event": "trw_recall"},
            {"ts": "2026-02-07T10:15:00Z", "event": "recall"},
        ]
        result = compute_run_velocity(events)
        assert result.learning_reuse_count == 3

    def test_invalid_timestamp_skipped(self) -> None:
        events = [
            {"ts": "not-a-date", "event": "run_init"},
            {"ts": "2026-02-07T10:00:00Z", "event": "done"},
        ]
        result = compute_run_velocity(events)
        assert result.total_duration_minutes == 0.0


# --- _compute_phase_durations ---


class TestComputePhaseDurations:
    """Tests for _compute_phase_durations."""

    def test_simple_phase(self) -> None:
        events = [
            {"ts": "2026-02-07T10:00:00Z", "event": "phase_enter", "phase": "research"},
            {"ts": "2026-02-07T10:30:00Z", "event": "phase_check", "phase": "research"},
        ]
        result = _compute_phase_durations(events)
        assert result["research"] == 30.0

    def test_no_matching_events(self) -> None:
        events = [
            {"ts": "2026-02-07T10:00:00Z", "event": "run_init"},
        ]
        result = _compute_phase_durations(events)
        assert result == {}

    def test_multiple_phases(self) -> None:
        events = [
            {"ts": "2026-02-07T10:00:00Z", "event": "phase_enter", "phase": "research"},
            {"ts": "2026-02-07T10:20:00Z", "event": "phase_check", "phase": "research"},
            {"ts": "2026-02-07T10:20:00Z", "event": "phase_enter", "phase": "plan"},
            {"ts": "2026-02-07T10:30:00Z", "event": "phase_check", "phase": "plan"},
        ]
        result = _compute_phase_durations(events)
        assert result["research"] == 20.0
        assert result["plan"] == 10.0


# --- compute_learning_effectiveness ---


class TestComputeLearningEffectiveness:
    """Tests for compute_learning_effectiveness."""

    def test_empty_dir(self, tmp_path: Path) -> None:
        entries_dir = tmp_path / "entries"
        result = compute_learning_effectiveness(entries_dir)
        assert result.active_count == 0
        assert result.effectiveness_ratio == 0.0

    def test_with_entries(self, tmp_path: Path) -> None:
        from trw_mcp.state.persistence import FileStateWriter

        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        writer = FileStateWriter()

        # Mature effective entry (q_value > 0.5, q_observations >= 3)
        writer.write_yaml(entries_dir / "learning-001.yaml", {
            "status": "active",
            "q_value": 0.8,
            "q_observations": 5,
        })
        # Mature ineffective entry
        writer.write_yaml(entries_dir / "learning-002.yaml", {
            "status": "active",
            "q_value": 0.3,
            "q_observations": 4,
        })
        # Immature entry
        writer.write_yaml(entries_dir / "learning-003.yaml", {
            "status": "active",
            "q_value": 0.9,
            "q_observations": 1,
        })
        # Resolved entry (should be skipped)
        writer.write_yaml(entries_dir / "learning-004.yaml", {
            "status": "resolved",
            "q_value": 0.9,
            "q_observations": 10,
        })

        result = compute_learning_effectiveness(entries_dir, cold_start_threshold=3)
        assert result.active_count == 3
        assert result.mature_count == 2
        assert result.effectiveness_ratio == 0.5  # 1 effective / 2 mature

    def test_custom_effective_threshold(self, tmp_path: Path) -> None:
        """With threshold=0.7, only q_value=0.8 is effective (0.3 and 0.9-immature excluded)."""
        from trw_mcp.state.persistence import FileStateWriter

        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        writer = FileStateWriter()

        writer.write_yaml(entries_dir / "a.yaml", {
            "status": "active", "q_value": 0.8, "q_observations": 5,
        })
        writer.write_yaml(entries_dir / "b.yaml", {
            "status": "active", "q_value": 0.6, "q_observations": 5,
        })

        # Default threshold (0.5): both are effective (0.8 > 0.5, 0.6 > 0.5)
        result = compute_learning_effectiveness(entries_dir, effective_q_threshold=0.5)
        assert result.effectiveness_ratio == 1.0

        # Higher threshold (0.7): only 0.8 is effective
        result = compute_learning_effectiveness(entries_dir, effective_q_threshold=0.7)
        assert result.effectiveness_ratio == 0.5


# --- compute_debt_indicators ---


class TestComputeDebtIndicators:
    """Tests for compute_debt_indicators."""

    def test_empty_dir(self, tmp_path: Path) -> None:
        result = compute_debt_indicators(tmp_path / "nonexistent")
        assert result.todo_count == 0

    def test_counts_todos(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "foo.py").write_text(
            "# TODO: fix this\n# FIXME: and this\nx = 1  # noqa\ny: int = 2  # type: ignore\n",
            encoding="utf-8",
        )
        result = compute_debt_indicators(src_dir)
        assert result.todo_count == 2
        assert result.lint_violation_estimate == 1
        assert result.mypy_ignore_count == 1

    def test_counts_test_skips(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_foo.py").write_text(
            "@pytest.mark.skip\ndef test_a(): pass\n\npytest.skip('reason')\n",
            encoding="utf-8",
        )
        result = compute_debt_indicators(src_dir, tests_dir)
        assert result.test_skip_count == 2


# --- compute_overhead_ratio ---


class TestComputeOverheadRatio:
    """Tests for compute_overhead_ratio."""

    def test_empty(self) -> None:
        result = compute_overhead_ratio([])
        assert result.framework_overhead_ratio == 0.0
        assert result.total_event_count == 0

    def test_mixed_events(self) -> None:
        events = [
            {"event": "run_init"},
            {"event": "checkpoint"},
            {"event": "shard_complete"},
            {"event": "shard_complete"},
            {"event": "reflection_complete"},
        ]
        result = compute_overhead_ratio(events)
        assert result.framework_op_count == 3
        assert result.total_event_count == 5
        assert result.framework_overhead_ratio == 0.6


# --- linear_fit ---


class TestLinearFit:
    """Tests for linear_fit (normal equations)."""

    def test_perfect_fit(self) -> None:
        x = [1.0, 2.0, 3.0, 4.0]
        y = [2.0, 4.0, 6.0, 8.0]
        slope, intercept, r_sq = linear_fit(x, y)
        assert slope == pytest.approx(2.0, abs=0.001)
        assert intercept == pytest.approx(0.0, abs=0.001)
        assert r_sq == pytest.approx(1.0, abs=0.001)

    def test_too_few_points(self) -> None:
        with pytest.raises(ValueError, match="Need >= 2"):
            linear_fit([1.0], [1.0])

    def test_zero_variance(self) -> None:
        with pytest.raises(ValueError, match="Zero variance"):
            linear_fit([1.0, 1.0, 1.0], [1.0, 2.0, 3.0])

    def test_float_epsilon_is_module_constant(self) -> None:
        assert _FLOAT_EPSILON == 1e-15

    def test_noisy_data(self) -> None:
        x = [0.0, 1.0, 2.0, 3.0, 4.0]
        y = [1.0, 2.1, 2.9, 4.2, 4.8]
        slope, intercept, r_sq = linear_fit(x, y)
        assert slope > 0
        assert r_sq > 0.9


# --- sign_test ---


class TestSignTest:
    """Tests for sign_test (binomial)."""

    def test_all_increasing(self) -> None:
        # Need 6 values → 5 diffs → binomial_tail(5,5) = 0.0625 < 0.1
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        direction, p_val = sign_test(values)
        assert direction == "accelerating"
        assert p_val < 0.1

    def test_all_decreasing(self) -> None:
        values = [6.0, 5.0, 4.0, 3.0, 2.0, 1.0]
        direction, p_val = sign_test(values)
        assert direction == "decelerating"
        assert p_val < 0.1

    def test_flat(self) -> None:
        values = [3.0, 3.0, 3.0, 3.0, 3.0]
        direction, p_val = sign_test(values)
        assert direction == "stable"
        assert p_val == 1.0

    def test_custom_alpha_strict(self) -> None:
        """With very strict alpha (0.01), the same data should be 'stable'."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        direction, p_val = sign_test(values, alpha=0.01)
        # p_value is ~0.0625 which is > 0.01 → stable
        assert direction == "stable"

    def test_too_few_values(self) -> None:
        with pytest.raises(ValueError, match="Need >= 5"):
            sign_test([1.0, 2.0, 3.0])


# --- binomial helpers ---


class TestBinomialHelpers:
    """Tests for _binom_pmf and _binomial_tail."""

    def test_binom_pmf_coin_flip(self) -> None:
        # P(X=2) for n=4, p=0.5 = C(4,2) * 0.5^4 = 6/16 = 0.375
        result = _binom_pmf(4, 2, 0.5)
        assert result == pytest.approx(0.375, abs=0.001)

    def test_binomial_tail_extreme(self) -> None:
        # P(X >= 4) for n=4, p=0.5, two-tailed
        result = _binomial_tail(4, 4)
        assert 0.0 < result <= 1.0


# --- detect_confounders ---


class TestDetectConfounders:
    """Tests for detect_confounders."""

    def test_no_confounders(self) -> None:
        history = [
            {"task": "t1", "framework_version": "v18", "learning_snapshot": {"active_count": 10}},
            {"task": "t1", "framework_version": "v18", "learning_snapshot": {"active_count": 11}},
        ]
        result = detect_confounders(history)
        assert result == []

    def test_task_heterogeneity(self) -> None:
        history = [
            {"task": "t1", "framework_version": "v18"},
            {"task": "t2", "framework_version": "v18"},
        ]
        result = detect_confounders(history)
        assert any("heterogeneity" in c.lower() for c in result)

    def test_framework_version_change(self) -> None:
        history = [
            {"task": "t1", "framework_version": "v17"},
            {"task": "t1", "framework_version": "v18"},
        ]
        result = detect_confounders(history)
        assert any("version" in c.lower() for c in result)

    def test_learning_count_jump(self) -> None:
        history = [
            {"task": "t1", "framework_version": "v18", "learning_snapshot": {"active_count": 10}},
            {"task": "t1", "framework_version": "v18", "learning_snapshot": {"active_count": 20}},
        ]
        result = detect_confounders(history)
        assert any("jumped" in c.lower() for c in result)

    def test_custom_jump_ratio(self) -> None:
        """With lower jump_ratio=1.2, a 30% increase triggers the confounder."""
        history = [
            {"task": "t1", "framework_version": "v18", "learning_snapshot": {"active_count": 10}},
            {"task": "t1", "framework_version": "v18", "learning_snapshot": {"active_count": 13}},
        ]
        # Default 1.5: 13 < 10*1.5=15 → no jump
        result = detect_confounders(history, jump_ratio=1.5)
        assert not any("jumped" in c.lower() for c in result)

        # Stricter 1.2: 13 > 10*1.2=12 → jump detected
        result = detect_confounders(history, jump_ratio=1.2)
        assert any("jumped" in c.lower() for c in result)

    def test_single_entry(self) -> None:
        result = detect_confounders([{"task": "t1"}])
        assert result == []


# --- compute_trend ---


class TestComputeTrend:
    """Tests for compute_trend."""

    def test_insufficient_data(self) -> None:
        result = compute_trend([{"metrics": {"shard_throughput": 1.0}}])
        assert result.direction == "insufficient_data"
        assert result.data_points == 1

    def test_improving_trend(self) -> None:
        history = [
            {"task": "t1", "framework_version": "v18", "metrics": {"shard_throughput": 1.0}},
            {"task": "t1", "framework_version": "v18", "metrics": {"shard_throughput": 2.0}},
            {"task": "t1", "framework_version": "v18", "metrics": {"shard_throughput": 3.0}},
            {"task": "t1", "framework_version": "v18", "metrics": {"shard_throughput": 4.0}},
        ]
        result = compute_trend(history)
        assert result.direction == "improving"
        assert result.linear_slope is not None
        assert result.linear_slope > 0

    def test_declining_trend(self) -> None:
        history = [
            {"task": "t1", "framework_version": "v18", "metrics": {"shard_throughput": 4.0}},
            {"task": "t1", "framework_version": "v18", "metrics": {"shard_throughput": 3.0}},
            {"task": "t1", "framework_version": "v18", "metrics": {"shard_throughput": 2.0}},
            {"task": "t1", "framework_version": "v18", "metrics": {"shard_throughput": 1.0}},
        ]
        result = compute_trend(history)
        assert result.direction == "declining"
        assert result.linear_slope is not None
        assert result.linear_slope < 0

    def test_stable_trend(self) -> None:
        history = [
            {"task": "t1", "framework_version": "v18", "metrics": {"shard_throughput": 5.0}},
            {"task": "t1", "framework_version": "v18", "metrics": {"shard_throughput": 5.0}},
            {"task": "t1", "framework_version": "v18", "metrics": {"shard_throughput": 5.0}},
        ]
        result = compute_trend(history)
        assert result.direction == "stable"

    def test_sign_test_runs_for_5_plus(self) -> None:
        history = [
            {"task": "t1", "framework_version": "v18", "metrics": {"shard_throughput": float(i)}}
            for i in range(1, 7)
        ]
        result = compute_trend(history)
        assert result.acceleration_direction is not None
        assert result.acceleration_p_value is not None
        assert result.data_points == 6
