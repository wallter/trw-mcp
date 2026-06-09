"""Edge-case tests for scoring decay date and entry-utility helpers."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.scoring import _days_since_access, _entry_utility


class TestDaysSinceAccessEdgeCases:
    """Additional edge cases for _days_since_access."""

    def test_none_string_in_field_skipped(self) -> None:
        """Fields with literal 'None' string are skipped."""
        today = date(2026, 3, 1)
        entry: dict[str, object] = {"last_accessed_at": "None", "created": "None"}
        result = _days_since_access(entry, today, fallback_days=77)
        assert result == 77

    def test_empty_string_field_skipped(self) -> None:
        """Empty string fields are skipped."""
        today = date(2026, 3, 1)
        entry: dict[str, object] = {"last_accessed_at": "", "created": "2026-02-25"}
        result = _days_since_access(entry, today)
        assert result == 4

    def test_fallback_days_none_uses_config_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When fallback_days is None, uses config scoring_default_days_unused."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        today = date(2026, 3, 1)
        entry: dict[str, object] = {}
        result = _days_since_access(entry, today, fallback_days=None)
        assert result == cfg.scoring_default_days_unused

    def test_future_date_returns_negative_days(self) -> None:
        """A future last_accessed_at returns negative days."""
        today = date(2026, 3, 1)
        entry: dict[str, object] = {"last_accessed_at": "2026-03-10"}
        result = _days_since_access(entry, today)
        assert result == -9

    def test_same_day_returns_zero(self) -> None:
        """Same-day access returns 0 days."""
        today = date(2026, 3, 1)
        entry: dict[str, object] = {"last_accessed_at": "2026-03-01"}
        result = _days_since_access(entry, today)
        assert result == 0

    def test_prefers_last_accessed_at_over_created(self) -> None:
        """last_accessed_at takes priority over created."""
        today = date(2026, 3, 1)
        entry: dict[str, object] = {
            "last_accessed_at": "2026-02-28",
            "created": "2026-01-01",
        }
        result = _days_since_access(entry, today)
        assert result == 1

    def test_datetime_string_with_time_component_parsed(self) -> None:
        """Full datetime strings (ISO 8601 with T separator) are accepted.

        Before the fix, date.fromisoformat('2026-02-28T12:00:00+00:00') raised
        ValueError on Python 3.12. Now datetime.fromisoformat handles these.
        """
        today = date(2026, 3, 1)
        entry: dict[str, object] = {"last_accessed_at": "2026-02-28T12:00:00+00:00"}
        result = _days_since_access(entry, today)
        assert result == 1

    def test_datetime_string_utc_z_suffix_parsed(self) -> None:
        """ISO 8601 UTC 'Z' suffix is normalised before parsing."""
        today = date(2026, 3, 1)
        entry: dict[str, object] = {"created": "2026-02-27T00:00:00Z"}
        result = _days_since_access(entry, today)
        assert result == 2


class TestEntryUtilityEdgeCases:
    """Edge cases for _entry_utility composite scoring."""

    def test_minimal_entry_no_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Entry with no scoring fields uses defaults."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        entry: dict[str, object] = {}
        result = _entry_utility(entry, datetime.now(tz=timezone.utc).date())
        assert 0.0 <= result <= 1.0

    def test_high_access_count_boosts_utility(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """High access_count should boost utility score."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        today = datetime.now(tz=timezone.utc).date()
        entry_low: dict[str, object] = {
            "impact": 0.5,
            "q_value": 0.5,
            "q_observations": 5,
            "recurrence": 1,
            "access_count": 0,
            "source_type": "agent",
            "created": today.isoformat(),
        }
        entry_high: dict[str, object] = {
            "impact": 0.5,
            "q_value": 0.5,
            "q_observations": 5,
            "recurrence": 1,
            "access_count": 50,
            "source_type": "agent",
            "created": today.isoformat(),
        }
        score_low = _entry_utility(entry_low, today)
        score_high = _entry_utility(entry_high, today)
        assert score_high >= score_low

    def test_human_source_boosts_utility(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """source_type='human' should produce higher utility than 'agent'."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        today = datetime.now(tz=timezone.utc).date()
        base: dict[str, object] = {
            "impact": 0.5,
            "q_value": 0.5,
            "q_observations": 5,
            "recurrence": 1,
            "access_count": 0,
            "created": today.isoformat(),
        }
        entry_agent = {**base, "source_type": "agent"}
        entry_human = {**base, "source_type": "human"}
        score_agent = _entry_utility(entry_agent, today)
        score_human = _entry_utility(entry_human, today)
        assert score_human >= score_agent

    def test_fallback_days_passed_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit fallback_days overrides config default."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        entry: dict[str, object] = {
            "impact": 0.5,
            "q_value": 0.5,
            "q_observations": 5,
            "recurrence": 1,
            "access_count": 0,
            "source_type": "agent",
        }
        score_fresh = _entry_utility(entry, datetime.now(tz=timezone.utc).date(), fallback_days=0)
        score_stale = _entry_utility(entry, datetime.now(tz=timezone.utc).date(), fallback_days=365)
        assert score_fresh > score_stale
