"""Property tests for apply_impact_decay."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from trw_mcp.scoring import _IMPACT_DECAY_FLOOR, apply_impact_decay

_impact = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


def _make_entry(impact: float, days_old: int) -> dict[str, object]:
    """Build a minimal learning entry dict for apply_impact_decay tests."""
    ref_dt = datetime.now(timezone.utc) - timedelta(days=days_old)
    return {
        "id": "test-id",
        "impact": impact,
        "last_accessed_at": ref_dt.isoformat(),
    }


@given(
    impact=st.floats(min_value=_IMPACT_DECAY_FLOOR, max_value=1.0, allow_nan=False, allow_infinity=False),
    days_old=st.integers(min_value=0, max_value=3000),
    half_life_days=st.integers(min_value=1, max_value=365),
)
@settings(max_examples=500)
def test_apply_impact_decay_floor_respected(
    impact: float,
    days_old: int,
    half_life_days: int,
) -> None:
    """apply_impact_decay never reduces impact below _IMPACT_DECAY_FLOOR (0.1)."""
    entries = [_make_entry(impact, days_old)]
    apply_impact_decay(entries, half_life_days=half_life_days)
    new_impact = float(str(entries[0]["impact"]))
    assert new_impact >= _IMPACT_DECAY_FLOOR - 1e-9, (
        f"new_impact={new_impact} below floor={_IMPACT_DECAY_FLOOR} "
        f"for impact={impact}, days_old={days_old}, half_life={half_life_days}"
    )


@given(
    impact=st.floats(min_value=_IMPACT_DECAY_FLOOR, max_value=1.0, allow_nan=False, allow_infinity=False),
    days_old=st.integers(min_value=0, max_value=3000),
    half_life_days=st.integers(min_value=1, max_value=365),
)
@settings(max_examples=500)
def test_apply_impact_decay_never_exceeds_original(
    impact: float,
    days_old: int,
    half_life_days: int,
) -> None:
    """apply_impact_decay never increases the impact score for entries at or above floor."""
    entries = [_make_entry(impact, days_old)]
    apply_impact_decay(entries, half_life_days=half_life_days)
    new_impact = float(str(entries[0]["impact"]))
    assert new_impact <= impact + 1e-9, (
        f"new_impact={new_impact} > original={impact} (days_old={days_old}, half_life={half_life_days})"
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
    days_old = max(0, half_life_days - 1)
    entries = [_make_entry(impact, days_old)]
    original_impact = float(str(entries[0]["impact"]))
    apply_impact_decay(entries, half_life_days=half_life_days)
    new_impact = float(str(entries[0]["impact"]))
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
    """apply_impact_decay on empty list is a no-op."""
    entries: list[dict[str, object]] = []
    apply_impact_decay(entries, half_life_days=half_life_days)
    assert entries == []
