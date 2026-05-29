"""Property tests for apply_time_decay."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from trw_mcp.scoring import apply_time_decay

_TIME_DECAY_FLOOR: float = 0.3
_impact = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
# Hypothesis 6.x: when ``timezones`` is supplied, ``min_value``/``max_value``
# must be naive — the timezone is applied separately by the strategy.
_past_datetime = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2026, 12, 31),
    timezones=st.just(timezone.utc),
)


@given(impact=_impact, created_at=_past_datetime)
@settings(max_examples=500)
def test_apply_time_decay_bounded(impact: float, created_at: datetime) -> None:
    """apply_time_decay output is always in [0.0, 1.0]."""
    result = apply_time_decay(impact, created_at)
    assert math.isfinite(result), f"Expected finite, got {result}"
    assert 0.0 <= result <= 1.0, f"Expected [0,1], got {result}"


@given(impact=_impact, created_at=_past_datetime)
@settings(max_examples=400)
def test_apply_time_decay_never_below_floor_times_impact(impact: float, created_at: datetime) -> None:
    """Decay factor is always >= _TIME_DECAY_FLOOR, so output >= impact * floor."""
    result = apply_time_decay(impact, created_at)
    lower_bound = impact * _TIME_DECAY_FLOOR
    assert result >= lower_bound - 1e-9, f"result={result} below floor bound={lower_bound} for impact={impact}"


@given(impact=_impact)
@settings(max_examples=300)
def test_apply_time_decay_zero_age_no_reduction(impact: float) -> None:
    """A freshly-created entry (now) gets decay_factor=1.0, so output equals input."""
    now = datetime.now(timezone.utc)
    result = apply_time_decay(impact, now)
    assert result == pytest.approx(impact, abs=1e-9), f"For days=0, expected output={impact}, got {result}"


@given(
    impact=st.floats(min_value=0.01, max_value=1.0, allow_nan=False, allow_infinity=False),
    days_old=st.integers(min_value=1, max_value=5000),
)
@settings(max_examples=400)
def test_apply_time_decay_monotonically_decreasing(impact: float, days_old: int) -> None:
    """Older entries get lower (or equal) decay than newer entries."""
    now = datetime.now(timezone.utc)
    newer = now - timedelta(days=days_old)
    older = now - timedelta(days=days_old + 1)

    result_newer = apply_time_decay(impact, newer)
    result_older = apply_time_decay(impact, older)

    assert result_older <= result_newer + 1e-9, (
        f"Older entry (days={days_old + 1}) scored {result_older} > newer entry (days={days_old}) {result_newer}"
    )
