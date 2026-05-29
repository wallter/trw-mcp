"""Property tests for enforce_tier_distribution."""

from __future__ import annotations

import math

from hypothesis import given, settings
from hypothesis import strategies as st

from trw_mcp.scoring import _TIER_HIGH_CEILING, _TIER_MEDIUM_CEILING, enforce_tier_distribution


def _make_entries(scores: list[float]) -> list[tuple[str, float]]:
    """Build (id, score) tuples for enforce_tier_distribution."""
    return [(f"L-{i:04d}", score) for i, score in enumerate(scores)]


@given(
    scores=st.lists(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        min_size=5,
        max_size=50,
    ),
)
@settings(max_examples=400)
def test_enforce_tier_distribution_demotions_stay_below_tier_ceiling(scores: list[float]) -> None:
    """Every demotion stays at or below the applicable tier ceiling."""
    demotions = enforce_tier_distribution(_make_entries(scores))
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
def test_enforce_tier_distribution_new_impact_less_than_original(scores: list[float]) -> None:
    """Every returned (id, new_impact) has new_impact strictly less than the original."""
    entries = _make_entries(scores)
    original = dict(entries)
    demotions = enforce_tier_distribution(entries)

    for lid, new_score in demotions:
        assert new_score < original[lid] + 1e-9, (
            f"Demotion of {lid}: new_score={new_score} not < original={original[lid]}"
        )


@given(
    scores=st.lists(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        min_size=5,
        max_size=50,
    ),
)
@settings(max_examples=400)
def test_enforce_tier_distribution_at_most_two_demotions(scores: list[float]) -> None:
    """A single call produces at most 2 demotions (one per tier: critical + high)."""
    demotions = enforce_tier_distribution(_make_entries(scores))
    assert len(demotions) <= 2, f"Expected at most 2 demotions per call, got {len(demotions)}: {demotions}"


@given(
    scores=st.lists(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        min_size=0,
        max_size=4,
    ),
)
@settings(max_examples=200)
def test_enforce_tier_distribution_small_set_no_demotions(scores: list[float]) -> None:
    """Sets with fewer than 5 entries never trigger demotions."""
    demotions = enforce_tier_distribution(_make_entries(scores))
    assert demotions == [], f"Expected no demotions for small set (size={len(scores)}), got {demotions}"


@given(
    scores=st.lists(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        min_size=5,
        max_size=50,
    ),
)
@settings(max_examples=300)
def test_enforce_tier_distribution_result_scores_finite(scores: list[float]) -> None:
    """All demotion target scores are finite (no NaN/inf)."""
    demotions = enforce_tier_distribution(_make_entries(scores))
    for _, new_score in demotions:
        assert math.isfinite(new_score), f"Non-finite demotion score: {new_score}"
        assert 0.0 <= new_score <= 1.0, f"Out-of-bounds demotion score: {new_score}"
