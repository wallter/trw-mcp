"""Event-timestamp and numeric helper coverage."""

from __future__ import annotations

import pytest

from trw_mcp.state._helpers import safe_float
from trw_mcp.state.report import _ts_diff_seconds


class TestTsDiffSecondsException:
    """Cover _ts_diff_seconds invalid timestamp handling."""

    def test_invalid_start_returns_none(self) -> None:
        """Non-parseable start timestamp returns None."""
        result = _ts_diff_seconds("not-a-timestamp", "2026-02-19T10:00:00Z")
        assert result is None

    def test_invalid_end_returns_none(self) -> None:
        """Non-parseable end timestamp returns None."""
        result = _ts_diff_seconds("2026-02-19T10:00:00Z", "also-invalid")
        assert result is None

    def test_both_invalid_returns_none(self) -> None:
        """Both timestamps invalid returns None."""
        result = _ts_diff_seconds("", "")
        assert result is None

    def test_valid_timestamps_return_seconds(self) -> None:
        """Sanity check: valid timestamps return correct elapsed seconds."""
        result = _ts_diff_seconds("2026-02-19T10:00:00Z", "2026-02-19T11:00:00Z")
        assert result == 3600.0


class TestSafeFloat:
    """Tests for safe_float from _helpers.py."""

    def test_string_non_numeric_returns_default(self) -> None:
        """Non-numeric string value returns default."""
        assert safe_float({"k": "not-a-number"}, "k", 0.0) == 0.0

    def test_none_value_returns_default(self) -> None:
        """None value returns default."""
        assert safe_float({"k": None}, "k", 0.0) == 0.0

    def test_list_value_returns_default(self) -> None:
        """List value returns default."""
        assert safe_float({"k": [1, 2, 3]}, "k", 0.0) == 0.0

    def test_int_converts_correctly(self) -> None:
        """Integer value converts to float correctly."""
        assert safe_float({"k": 42}, "k", 0.0) == pytest.approx(42.0)

    def test_float_passthrough(self) -> None:
        """Float value passes through unchanged."""
        assert safe_float({"k": 0.75}, "k", 0.0) == pytest.approx(0.75)

    def test_numeric_string_converts(self) -> None:
        """Numeric string converts to float."""
        assert safe_float({"k": "0.85"}, "k", 0.0) == pytest.approx(0.85)

    def test_missing_key_returns_default(self) -> None:
        """Missing key returns default value."""
        assert safe_float({}, "missing", 0.5) == pytest.approx(0.5)
