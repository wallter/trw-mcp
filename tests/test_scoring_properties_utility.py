"""Property tests for compute_utility_score."""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from trw_mcp.scoring import compute_utility_score

_impact = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_nonneg_int = st.integers(min_value=0, max_value=10_000)
_pos_int = st.integers(min_value=1, max_value=10_000)
_q_value = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


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
    """Higher base_impact -> higher (or equal) utility when other params are fixed."""
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
    assert low <= high, f"Expected utility(impact={impact_low}) <= utility(impact={impact_high}), got {low} > {high}"


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
    """impact=0 and q_value=0 with no access boost yields utility=0.0."""
    result = compute_utility_score(
        q_value=0.0,
        days_since_last_access=days_since_last_access,
        recurrence_count=recurrence_count,
        base_impact=0.0,
        q_observations=0,
        access_count=0,
        source_type="agent",
    )
    assert result == pytest.approx(0.0, abs=1e-9), f"Expected 0.0 for zero-impact/zero-q, got {result}"
