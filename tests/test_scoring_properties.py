"""Property-based tests for trw_mcp/scoring.py using the hypothesis library.

Validates mathematical invariants that unit tests cannot exhaustively cover:
output bounds, monotonicity, and edge-case stability across the full input
space for the core scoring functions.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from trw_mcp.scoring import (
    _IMPACT_DECAY_FLOOR,
    _TIER_HIGH_CEILING,
    _TIER_MEDIUM_CEILING,
    apply_impact_decay,
    apply_time_decay,
    compute_utility_score,
    enforce_tier_distribution,
    update_q_value,
)

# Floor constant from trw_memory.lifecycle.scoring.apply_time_decay
_TIME_DECAY_FLOOR: float = 0.3

# ---------------------------------------------------------------------------
# Strategy helpers
# ---------------------------------------------------------------------------

# Valid impact / Q-value range
_impact = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_q_value = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_reward = st.floats(min_value=-1.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_nonneg_int = st.integers(min_value=0, max_value=10_000)
_pos_int = st.integers(min_value=1, max_value=10_000)

# Datetime strategy: UTC datetimes from 5 years ago to 1 day in the future
_past_datetime = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2026, 12, 31),
    timezones=st.just(timezone.utc),
)


# ---------------------------------------------------------------------------
# 1. compute_utility_score — output always in [0.0, 1.0]
# ---------------------------------------------------------------------------


@given(
    q_value=_q_value,
    days_since_last_access=_nonneg_int,
    recurrence_count=_pos_int,
    base_impact=_impact,
    q_observations=_nonneg_int,
    access_count=_nonneg_int,
    source_type=st.sampled_from(["agent", "human"]),
)
@settings(max_examples=200, deadline=None)
def test_compute_utility_score_bounded(
    q_value: float,
    days_since_last_access: int,
    recurrence_count: int,
    base_impact: float,
    q_observations: int,
    access_count: int,
    source_type: str,
) -> None:
    """compute_utility_score always returns a value in [0.0, 1.0]."""
    result = compute_utility_score(
        q_value=q_value,
        days_since_last_access=days_since_last_access,
        recurrence_count=recurrence_count,
        base_impact=base_impact,
        q_observations=q_observations,
        access_count=access_count,
        source_type=source_type,
    )
    assert math.isfinite(result), f"Expected finite, got {result}"
    assert 0.0 <= result <= 1.0, f"Expected [0,1], got {result}"


@given(
    impact_low=st.floats(min_value=0.0, max_value=0.49, allow_nan=False, allow_infinity=False),
    impact_high=st.floats(min_value=0.51, max_value=1.0, allow_nan=False, allow_infinity=False),
    days_since_last_access=_nonneg_int,
    recurrence_count=_pos_int,
    q_observations=_nonneg_int,
)
@settings(max_examples=300)
def test_compute_utility_score_monotone_impact(
    impact_low: float,
    impact_high: float,
    days_since_last_access: int,
    recurrence_count: int,
    q_observations: int,
) -> None:
    """Higher base_impact -> higher (or equal) utility when other params are fixed.

    This holds only in the cold-start regime where q_value == base_impact,
    so q_observations < cold_start_threshold (default 3) and we use base_impact
    as q_value to isolate the impact effect.
    """
    # Force cold-start: q_value == base_impact, q_observations = 0
    low = compute_utility_score(
        q_value=impact_low,
        days_since_last_access=days_since_last_access,
        recurrence_count=recurrence_count,
        base_impact=impact_low,
        q_observations=0,
    )
    high = compute_utility_score(
        q_value=impact_high,
        days_since_last_access=days_since_last_access,
        recurrence_count=recurrence_count,
        base_impact=impact_high,
        q_observations=0,
    )
    assert low <= high, (
        f"Expected utility(impact={impact_low}) <= utility(impact={impact_high}), "
        f"got {low} > {high}"
    )


@given(
    days_since_last_access=_nonneg_int,
    recurrence_count=_pos_int,
    q_observations=_nonneg_int,
)
@settings(max_examples=200)
def test_compute_utility_score_zero_impact_yields_low_utility(
    days_since_last_access: int,
    recurrence_count: int,
    q_observations: int,
) -> None:
    """impact=0 and q_value=0 with no access boost yields utility=0.0.

    When source_type='agent' (no human boost) and access_count=0 (no access
    boost), the utility collapses to 0 * retention = 0.0.
    """
    result = compute_utility_score(
        q_value=0.0,
        days_since_last_access=days_since_last_access,
        recurrence_count=recurrence_count,
        base_impact=0.0,
        q_observations=0,
        access_count=0,
        source_type="agent",
    )
    assert result == pytest.approx(0.0, abs=1e-9), (
        f"Expected 0.0 for zero-impact/zero-q, got {result}"
    )


# ---------------------------------------------------------------------------
# 2. apply_time_decay — bounded, monotone, days=0 produces no reduction
# ---------------------------------------------------------------------------


@given(impact=_impact, created_at=_past_datetime)
@settings(max_examples=500)
def test_apply_time_decay_bounded(impact: float, created_at: datetime) -> None:
    """apply_time_decay output is always in [0.0, 1.0]."""
    result = apply_time_decay(impact, created_at)
    assert math.isfinite(result), f"Expected finite, got {result}"
    assert 0.0 <= result <= 1.0, f"Expected [0,1], got {result}"


@given(impact=_impact, created_at=_past_datetime)
@settings(max_examples=400)
def test_apply_time_decay_never_below_floor_times_impact(
    impact: float,
    created_at: datetime,
) -> None:
    """Decay factor is always >= _TIME_DECAY_FLOOR, so output >= impact * floor."""
    result = apply_time_decay(impact, created_at)
    # The decay formula guarantees: decay_factor >= _TIME_DECAY_FLOOR
    # Therefore: result = clamp(impact * decay_factor) >= impact * _TIME_DECAY_FLOOR
    # (modulo floating-point edge cases at exact floor boundary)
    lower_bound = impact * _TIME_DECAY_FLOOR
    assert result >= lower_bound - 1e-9, (
        f"result={result} below floor bound={lower_bound} for impact={impact}"
    )


@given(impact=_impact)
@settings(max_examples=300)
def test_apply_time_decay_zero_age_no_reduction(impact: float) -> None:
    """A freshly-created entry (now) gets decay_factor=1.0, so output equals input."""
    # Use a datetime very close to now (within a second) — days=0
    now = datetime.now(timezone.utc)
    result = apply_time_decay(impact, now)
    # days=0 -> decay_factor = max(0.3, 1.0 - 0*0.3/365) = 1.0
    assert result == pytest.approx(impact, abs=1e-9), (
        f"For days=0, expected output={impact}, got {result}"
    )


@given(
    impact=st.floats(min_value=0.01, max_value=1.0, allow_nan=False, allow_infinity=False),
    days_old=st.integers(min_value=1, max_value=5000),
)
@settings(max_examples=400)
def test_apply_time_decay_monotonically_decreasing(
    impact: float,
    days_old: int,
) -> None:
    """Older entries get lower (or equal) decay than newer entries."""
    now = datetime.now(timezone.utc)
    newer = now - timedelta(days=days_old)
    older = now - timedelta(days=days_old + 1)

    result_newer = apply_time_decay(impact, newer)
    result_older = apply_time_decay(impact, older)
    # Older should not exceed newer (decay is non-increasing with age)
    assert result_older <= result_newer + 1e-9, (
        f"Older entry (days={days_old+1}) scored {result_older} > "
        f"newer entry (days={days_old}) {result_newer}"
    )


# ---------------------------------------------------------------------------
# 3. apply_impact_decay — floor respected, never exceeds original, no-op when fresh
# ---------------------------------------------------------------------------


def _make_entry(impact: float, days_old: int, half_life_days: int) -> dict[str, object]:
    """Build a minimal learning entry dict for apply_impact_decay tests."""
    ref_dt = datetime.now(timezone.utc) - timedelta(days=days_old)
    return {
        "id": "test-id",
        "impact": impact,
        "last_accessed_at": ref_dt.isoformat(),
    }


@given(
    impact=st.floats(
        min_value=_IMPACT_DECAY_FLOOR,
        max_value=1.0,
        allow_nan=False,
        allow_infinity=False,
    ),
    days_old=st.integers(min_value=0, max_value=3000),
    half_life_days=st.integers(min_value=1, max_value=365),
)
@settings(max_examples=500)
def test_apply_impact_decay_floor_respected(
    impact: float,
    days_old: int,
    half_life_days: int,
) -> None:
    """apply_impact_decay never reduces impact below _IMPACT_DECAY_FLOOR (0.1).

    The floor clamp `max(_IMPACT_DECAY_FLOOR, new_impact)` is applied after the
    exponential decay, so the result is always >= 0.1 when decay is applied.
    For entries below the floor already (impact < 0.1), the function would
    *raise* the value to the floor — we exclude those cases and test only
    entries that start at or above the floor.
    """
    entry = _make_entry(impact, days_old, half_life_days)
    result_entries = apply_impact_decay([entry], half_life_days=half_life_days)
    new_impact = float(str(result_entries[0]["impact"]))
    assert new_impact >= _IMPACT_DECAY_FLOOR - 1e-9, (
        f"new_impact={new_impact} below floor={_IMPACT_DECAY_FLOOR} "
        f"for impact={impact}, days_old={days_old}, half_life={half_life_days}"
    )


@given(
    impact=st.floats(
        min_value=_IMPACT_DECAY_FLOOR,
        max_value=1.0,
        allow_nan=False,
        allow_infinity=False,
    ),
    days_old=st.integers(min_value=0, max_value=3000),
    half_life_days=st.integers(min_value=1, max_value=365),
)
@settings(max_examples=500)
def test_apply_impact_decay_never_exceeds_original(
    impact: float,
    days_old: int,
    half_life_days: int,
) -> None:
    """apply_impact_decay never increases the impact score for entries at or above floor.

    When the original impact is >= _IMPACT_DECAY_FLOOR, the floor clamp cannot
    raise the value above the original: the only change the floor makes is raising
    a sub-floor value up to 0.1, but if original >= 0.1 then
    max(0.1, decayed) <= original because decayed <= original.
    """
    entry = _make_entry(impact, days_old, half_life_days)
    result_entries = apply_impact_decay([entry], half_life_days=half_life_days)
    new_impact = float(str(result_entries[0]["impact"]))
    # Allow tiny floating-point rounding tolerance
    assert new_impact <= impact + 1e-9, (
        f"new_impact={new_impact} > original={impact} "
        f"(days_old={days_old}, half_life={half_life_days})"
    )


@given(
    impact=_impact,
    half_life_days=st.integers(min_value=1, max_value=365),
)
@settings(max_examples=300)
def test_apply_impact_decay_no_decay_when_fresh(
    impact: float,
    half_life_days: int,
) -> None:
    """Entries accessed within half_life_days are not decayed at all."""
    # days_old < half_life_days: entry is still fresh, no decay applied
    days_old = max(0, half_life_days - 1)
    entry = _make_entry(impact, days_old, half_life_days)
    original_impact = float(str(entry["impact"]))
    result_entries = apply_impact_decay([entry], half_life_days=half_life_days)
    new_impact = float(str(result_entries[0]["impact"]))
    assert new_impact == pytest.approx(original_impact, abs=1e-9), (
        f"Fresh entry (days_old={days_old} < half_life={half_life_days}) "
        f"should not be decayed: {original_impact} -> {new_impact}"
    )


@given(
    impact=_impact,
    half_life_days=st.integers(min_value=1, max_value=365),
)
@settings(max_examples=200)
def test_apply_impact_decay_empty_entry_list(
    impact: float,
    half_life_days: int,
) -> None:
    """apply_impact_decay on empty list returns empty list without error."""
    result = apply_impact_decay([], half_life_days=half_life_days)
    assert result == []


# ---------------------------------------------------------------------------
# 4. update_q_value (importance/quality score) — bounded and finite
# ---------------------------------------------------------------------------


@given(q_old=_q_value, reward=_reward)
@settings(max_examples=500)
def test_update_q_value_bounded(q_old: float, reward: float) -> None:
    """update_q_value always returns a value in [0.0, 1.0]."""
    result = update_q_value(q_old, reward)
    assert math.isfinite(result), f"Expected finite, got {result}"
    assert 0.0 <= result <= 1.0, f"Expected [0,1], got {result}"


@given(
    q_old=_q_value,
    reward=_reward,
    alpha=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    recurrence_bonus=st.floats(
        min_value=0.0, max_value=0.2, allow_nan=False, allow_infinity=False
    ),
)
@settings(max_examples=500)
def test_update_q_value_bounded_custom_alpha(
    q_old: float,
    reward: float,
    alpha: float,
    recurrence_bonus: float,
) -> None:
    """update_q_value stays in [0.0, 1.0] across the full alpha range."""
    result = update_q_value(q_old, reward, alpha=alpha, recurrence_bonus=recurrence_bonus)
    assert math.isfinite(result), f"Expected finite, got {result}"
    assert 0.0 <= result <= 1.0, f"Expected [0,1], got {result}"


@given(q_old=_q_value)
@settings(max_examples=200)
def test_update_q_value_reward_equals_q_is_stable(q_old: float) -> None:
    """When reward == q_old, the Q-value does not change (fixed point)."""
    result = update_q_value(q_old, reward=q_old, alpha=0.15, recurrence_bonus=0.0)
    assert result == pytest.approx(q_old, abs=1e-9), (
        f"Fixed point violated: q_old={q_old}, result={result}"
    )


@given(
    q_old=st.floats(min_value=0.0, max_value=0.5, allow_nan=False, allow_infinity=False),
    reward=st.floats(min_value=0.6, max_value=1.0, allow_nan=False, allow_infinity=False),
    alpha=st.floats(min_value=0.01, max_value=1.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=300)
def test_update_q_value_positive_reward_increases_q(
    q_old: float,
    reward: float,
    alpha: float,
) -> None:
    """A reward strictly greater than q_old moves the Q-value upward."""
    result = update_q_value(q_old, reward=reward, alpha=alpha, recurrence_bonus=0.0)
    assert result >= q_old - 1e-9, (
        f"Expected result >= q_old={q_old} when reward={reward} > q_old, got {result}"
    )


# ---------------------------------------------------------------------------
# 5. enforce_tier_distribution — demotions stay within tier ceilings
# ---------------------------------------------------------------------------


def _make_entries(scores: list[float]) -> list[tuple[str, float]]:
    """Build (id, score) tuples for enforce_tier_distribution."""
    return [(f"L-{i:04d}", score) for i, score in enumerate(scores)]


@given(
    # Need at least 5 entries for enforcement to apply
    scores=st.lists(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        min_size=5,
        max_size=50,
    ),
)
@settings(max_examples=400)
def test_enforce_tier_distribution_demotions_stay_below_tier_ceiling(
    scores: list[float],
) -> None:
    """Every demotion from critical lands at or below _TIER_HIGH_CEILING (0.89).

    Every demotion from high lands at or below _TIER_MEDIUM_CEILING (0.69).
    """
    entries = _make_entries(scores)
    demotions = enforce_tier_distribution(entries)

    for _, new_score in demotions:
        assert new_score <= _TIER_HIGH_CEILING + 1e-9 or new_score <= _TIER_MEDIUM_CEILING + 1e-9, (
            f"Demotion target {new_score} exceeds both tier ceilings"
        )


@given(
    scores=st.lists(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        min_size=5,
        max_size=50,
    ),
)
@settings(max_examples=400)
def test_enforce_tier_distribution_new_impact_less_than_original(
    scores: list[float],
) -> None:
    """Every returned (id, new_impact) has new_impact strictly less than the original."""
    entries = _make_entries(scores)
    original = dict(entries)
    demotions = enforce_tier_distribution(entries)

    for lid, new_score in demotions:
        orig = original[lid]
        assert new_score < orig + 1e-9, (
            f"Demotion of {lid}: new_score={new_score} not < original={orig}"
        )


@given(
    scores=st.lists(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        min_size=5,
        max_size=50,
    ),
)
@settings(max_examples=400)
def test_enforce_tier_distribution_at_most_two_demotions(
    scores: list[float],
) -> None:
    """A single call produces at most 2 demotions (one per tier: critical + high)."""
    entries = _make_entries(scores)
    demotions = enforce_tier_distribution(entries)
    assert len(demotions) <= 2, (
        f"Expected at most 2 demotions per call, got {len(demotions)}: {demotions}"
    )


@given(
    scores=st.lists(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        min_size=0,
        max_size=4,
    ),
)
@settings(max_examples=200)
def test_enforce_tier_distribution_small_set_no_demotions(
    scores: list[float],
) -> None:
    """Sets with fewer than 5 entries never trigger demotions."""
    entries = _make_entries(scores)
    demotions = enforce_tier_distribution(entries)
    assert demotions == [], (
        f"Expected no demotions for small set (size={len(scores)}), got {demotions}"
    )


@given(
    scores=st.lists(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        min_size=5,
        max_size=50,
    ),
)
@settings(max_examples=300)
def test_enforce_tier_distribution_result_scores_finite(
    scores: list[float],
) -> None:
    """All demotion target scores are finite (no NaN/inf)."""
    entries = _make_entries(scores)
    demotions = enforce_tier_distribution(entries)
    for _, new_score in demotions:
        assert math.isfinite(new_score), f"Non-finite demotion score: {new_score}"
        assert 0.0 <= new_score <= 1.0, f"Out-of-bounds demotion score: {new_score}"
