"""Edge-case tests for scoring constants and utility helpers."""

from __future__ import annotations

import math

import pytest

from trw_mcp.scoring import (
    _IMPACT_DECAY_FLOOR,
    _LN2,
    _TIER_HIGH_CEILING,
    _TIER_MEDIUM_CEILING,
    _clamp01,
    safe_float,
    safe_int,
)


class TestScoringConstants:
    """Verify scoring constants have expected mathematical values."""

    def test_ln2_value(self) -> None:
        assert _LN2 == pytest.approx(math.log(2), abs=1e-10)

    def test_impact_decay_floor(self) -> None:
        assert _IMPACT_DECAY_FLOOR == 0.1

    def test_tier_high_ceiling(self) -> None:
        assert _TIER_HIGH_CEILING == 0.89

    def test_tier_medium_ceiling(self) -> None:
        assert _TIER_MEDIUM_CEILING == 0.69


class TestClamp01:
    """Tests for _clamp01 -- clamping to [0.0, 1.0]."""

    def test_within_range_unchanged(self) -> None:
        assert _clamp01(0.5) == 0.5

    def test_below_zero_clamped(self) -> None:
        assert _clamp01(-0.5) == 0.0

    def test_above_one_clamped(self) -> None:
        assert _clamp01(1.5) == 1.0

    def test_exact_zero(self) -> None:
        assert _clamp01(0.0) == 0.0

    def test_exact_one(self) -> None:
        assert _clamp01(1.0) == 1.0

    def test_very_small_positive(self) -> None:
        assert _clamp01(1e-15) == pytest.approx(1e-15)

    def test_very_large_negative(self) -> None:
        assert _clamp01(-1e10) == 0.0


class TestSafeFloatEdgeCases:
    """Additional edge cases for safe_float."""

    def test_none_value_returns_default(self) -> None:
        assert safe_float({"x": None}, "x", 0.5) == pytest.approx(0.5)

    def test_boolean_true_returns_default(self) -> None:
        """str(True) = 'True' which float() can't parse, returns default."""
        result = safe_float({"x": True}, "x", 0.0)
        assert result == pytest.approx(0.0)

    def test_boolean_false_returns_default(self) -> None:
        """str(False) = 'False' which float() can't parse, returns default."""
        result = safe_float({"x": False}, "x", 0.5)
        assert result == pytest.approx(0.5)

    def test_non_numeric_string_returns_default(self) -> None:
        assert safe_float({"x": "abc"}, "x", 0.42) == pytest.approx(0.42)

    def test_integer_value_coerced_to_float(self) -> None:
        assert safe_float({"x": 7}, "x", 0.0) == pytest.approx(7.0)

    def test_empty_string_returns_default(self) -> None:
        assert safe_float({"x": ""}, "x", 0.33) == pytest.approx(0.33)

    def test_whitespace_string_returns_default(self) -> None:
        assert safe_float({"x": "  "}, "x", 0.99) == pytest.approx(0.99)


class TestSafeIntEdgeCases:
    """Additional edge cases for safe_int."""

    def test_none_value_returns_default(self) -> None:
        assert safe_int({"x": None}, "x", 10) == 10

    def test_float_value_returns_default(self) -> None:
        """int(str(3.7)) = int('3.7') raises ValueError, returns default."""
        result = safe_int({"x": 3.7}, "x", 0)
        assert result == 0

    def test_non_numeric_string_returns_default(self) -> None:
        assert safe_int({"x": "abc"}, "x", 42) == 42

    def test_negative_int_preserved(self) -> None:
        assert safe_int({"x": -5}, "x", 0) == -5

    def test_zero_value_not_confused_with_missing(self) -> None:
        assert safe_int({"x": 0}, "x", 99) == 0
