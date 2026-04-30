"""Property tests for update_q_value."""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from trw_mcp.scoring import update_q_value

_q_value = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_reward = st.floats(min_value=-1.0, max_value=1.0, allow_nan=False, allow_infinity=False)


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
    recurrence_bonus=st.floats(min_value=0.0, max_value=0.2, allow_nan=False, allow_infinity=False),
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
    assert result == pytest.approx(q_old, abs=1e-9), f"Fixed point violated: q_old={q_old}, result={result}"


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
    assert result >= q_old - 1e-9, f"Expected result >= q_old={q_old} when reward={reward} > q_old, got {result}"
