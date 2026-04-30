"""Branch tests for scoring helpers."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

import trw_mcp.scoring as scoring_mod


class TestDaysSinceAccess:
    """Tests for _days_since_access helper."""

    def test_uses_last_accessed_at(self) -> None:
        """Uses last_accessed_at when available."""
        today = date(2026, 2, 22)
        entry = {"last_accessed_at": "2026-02-15"}
        result = scoring_mod._days_since_access(entry, today)
        assert result == 7

    def test_falls_back_to_created(self) -> None:
        """Falls back to created field when last_accessed_at missing."""
        today = date(2026, 2, 22)
        entry = {"created": "2026-02-01"}
        result = scoring_mod._days_since_access(entry, today)
        assert result == 21

    def test_uses_fallback_days_when_no_dates(self) -> None:
        """Returns fallback_days when no date fields present."""
        today = date(2026, 2, 22)
        entry: dict[str, object] = {}
        result = scoring_mod._days_since_access(entry, today, fallback_days=99)
        assert result == 99

    def test_invalid_date_skipped(self) -> None:
        """Invalid date string falls back to created or fallback_days."""
        today = date(2026, 2, 22)
        entry = {"last_accessed_at": "not-a-date", "created": "2026-02-20"}
        result = scoring_mod._days_since_access(entry, today)
        assert result == 2

    def test_both_invalid_uses_fallback(self) -> None:
        """Both invalid dates uses fallback_days."""
        today = date(2026, 2, 22)
        entry = {"last_accessed_at": "bad", "created": "also-bad"}
        result = scoring_mod._days_since_access(entry, today, fallback_days=42)
        assert result == 42


class TestEnsureUtc:
    """Tests for _ensure_utc helper."""

    def test_naive_datetime_gets_utc(self) -> None:
        """Naive datetime gets UTC timezone assigned."""
        aware = datetime(2026, 2, 22, 10, 0, 0, tzinfo=timezone.utc)
        naive = aware.replace(tzinfo=None)
        result = scoring_mod._ensure_utc(naive)
        assert result.tzinfo == timezone.utc

    def test_aware_datetime_unchanged(self) -> None:
        """Already timezone-aware datetime is returned unchanged."""
        aware = datetime(2026, 2, 22, 10, 0, 0, tzinfo=timezone.utc)
        result = scoring_mod._ensure_utc(aware)
        assert result == aware


class TestFieldExtractors:
    """Tests for safe_float and safe_int helpers (canonical dict extractors)."""

    def test_float_field_present(self) -> None:
        assert scoring_mod.safe_float({"impact": 0.75}, "impact", 0.5) == pytest.approx(0.75)

    def test_float_field_missing_uses_default(self) -> None:
        assert scoring_mod.safe_float({}, "impact", 0.5) == pytest.approx(0.5)

    def test_float_field_string_coercion(self) -> None:
        assert scoring_mod.safe_float({"impact": "0.9"}, "impact", 0.0) == pytest.approx(0.9)

    def test_int_field_present(self) -> None:
        assert scoring_mod.safe_int({"recurrence": 5}, "recurrence", 1) == 5

    def test_int_field_missing_uses_default(self) -> None:
        assert scoring_mod.safe_int({}, "recurrence", 1) == 1

    def test_int_field_string_coercion(self) -> None:
        assert scoring_mod.safe_int({"recurrence": "3"}, "recurrence", 0) == 3
