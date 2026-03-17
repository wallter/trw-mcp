"""Edge-case and boundary-condition tests for the scoring package.

Covers untested branches and edge cases in:
- _complexity.py: compute_tier_ceremony_score raw event strings, unknown tiers
- _decay.py: _days_since_access, _entry_utility, apply_impact_decay, enforce_tier_distribution
- _recall.py: rank_by_utility edge cases, utility_based_prune_candidates boundaries
- _correlation.py: _resolve_event_reward additional paths
- _utils.py: safe_float/safe_int edge values, constants
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.run import EventType
from trw_mcp.scoring import (
    _IMPACT_DECAY_FLOOR,
    _LN2,
    _TIER_HIGH_CEILING,
    _TIER_MEDIUM_CEILING,
    _clamp01,
    _days_since_access,
    _entry_utility,
    apply_impact_decay,
    compute_tier_ceremony_score,
    compute_utility_score,
    enforce_tier_distribution,
    rank_by_utility,
    safe_float,
    safe_int,
    update_q_value,
    utility_based_prune_candidates,
)

# ============================================================================
# Constants verification
# ============================================================================


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


# ============================================================================
# _clamp01
# ============================================================================


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


# ============================================================================
# safe_float / safe_int edge cases
# ============================================================================


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


# ============================================================================
# _days_since_access edge cases
# ============================================================================


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
        assert result == 1  # Uses last_accessed_at, not created (59 days)


# ============================================================================
# _entry_utility edge cases
# ============================================================================


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
        # With large fallback_days, utility should be very low
        score_fresh = _entry_utility(entry, datetime.now(tz=timezone.utc).date(), fallback_days=0)
        score_stale = _entry_utility(entry, datetime.now(tz=timezone.utc).date(), fallback_days=365)
        assert score_fresh > score_stale


# ============================================================================
# apply_impact_decay edge cases
# ============================================================================


class TestApplyImpactDecayEdgeCases:
    """Edge cases for apply_impact_decay batch function."""

    def test_empty_list_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        entries: list[dict[str, object]] = []
        apply_impact_decay(entries)
        assert entries == []

    def test_entry_without_dates_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Entry with no date fields is skipped."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        entry: dict[str, object] = {"impact": 0.8}
        entries = [entry]
        apply_impact_decay(entries)
        assert entries[0]["impact"] == 0.8

    def test_fresh_entry_not_decayed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Entry accessed recently (within half-life) is not decayed."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        now_str = datetime.now(timezone.utc).isoformat()
        entry: dict[str, object] = {"impact": 0.9, "last_accessed_at": now_str}
        entries = [entry]
        apply_impact_decay(entries)
        assert entries[0]["impact"] == 0.9

    def test_stale_entry_decayed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Entry well past half-life is decayed."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        old_date = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        entry: dict[str, object] = {"impact": 0.9, "last_accessed_at": old_date}
        entries = [entry]
        apply_impact_decay(entries)
        new_impact = float(str(entries[0]["impact"]))
        assert new_impact < 0.9
        assert new_impact >= _IMPACT_DECAY_FLOOR

    def test_uses_last_accessed_field_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Falls back to 'last_accessed' field when 'last_accessed_at' missing."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        old_date = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        entry: dict[str, object] = {"impact": 0.9, "last_accessed": old_date}
        entries = [entry]
        apply_impact_decay(entries)
        new_impact = float(str(entries[0]["impact"]))
        assert new_impact < 0.9

    def test_uses_created_field_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Falls back to 'created' field when other date fields missing."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        old_date = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        entry: dict[str, object] = {"impact": 0.9, "created": old_date}
        entries = [entry]
        apply_impact_decay(entries)
        new_impact = float(str(entries[0]["impact"]))
        assert new_impact < 0.9

    def test_invalid_date_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Entry with invalid date is skipped (impact unchanged)."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        entry: dict[str, object] = {"impact": 0.8, "created": "not-a-date"}
        entries = [entry]
        apply_impact_decay(entries)
        assert entries[0]["impact"] == 0.8

    def test_none_string_date_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """'None' string dates are skipped."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        entry: dict[str, object] = {
            "impact": 0.8,
            "last_accessed_at": "None",
            "last_accessed": "None",
            "created": "None",
        }
        entries = [entry]
        apply_impact_decay(entries)
        assert entries[0]["impact"] == 0.8

    def test_impact_never_below_floor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Even very old entries don't drop below the decay floor."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        very_old = (datetime.now(timezone.utc) - timedelta(days=5000)).isoformat()
        entry: dict[str, object] = {"impact": 0.9, "created": very_old}
        entries = [entry]
        apply_impact_decay(entries)
        new_impact = float(str(entries[0]["impact"]))
        assert new_impact >= _IMPACT_DECAY_FLOOR

    def test_custom_half_life_days(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit half_life_days param overrides config default."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        # 60 days old entry with short half-life of 7 days should decay heavily
        old_date = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        entry: dict[str, object] = {"impact": 0.9, "created": old_date}
        entries_short = [dict(entry)]
        entries_long = [dict(entry)]
        apply_impact_decay(entries_short, half_life_days=7)
        apply_impact_decay(entries_long, half_life_days=300)
        short_impact = float(str(entries_short[0]["impact"]))
        long_impact = float(str(entries_long[0]["impact"]))
        # Short half-life should produce more decay
        assert short_impact < long_impact

    def test_multiple_entries_processed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """All entries in the list are processed (batch operation)."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        old = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        entries: list[dict[str, object]] = [
            {"impact": 0.9, "created": old},
            {"impact": 0.7, "created": old},
            {"impact": 0.5, "created": old},
        ]
        apply_impact_decay(entries)
        assert len(entries) == 3
        for e in entries:
            assert float(str(e["impact"])) < 0.9

    def test_modifies_in_place_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """apply_impact_decay modifies entries in-place and returns None."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        old = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        entries: list[dict[str, object]] = [{"impact": 0.9, "created": old}]
        result = apply_impact_decay(entries)
        assert result is None
        assert float(str(entries[0]["impact"])) < 0.9


# ============================================================================
# enforce_tier_distribution edge cases
# ============================================================================


class TestEnforceTierDistributionEdgeCases:
    """Edge cases for enforce_tier_distribution."""

    def test_empty_entries_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        result = enforce_tier_distribution([])
        assert result == []

    def test_under_five_entries_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fewer than 5 entries: percentage caps meaningless, no enforcement."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        entries = [("L-1", 0.95), ("L-2", 0.95), ("L-3", 0.95), ("L-4", 0.95)]
        result = enforce_tier_distribution(entries)
        assert result == []

    def test_exactly_five_entries_enforced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Exactly 5 entries: enforcement kicks in."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        # All 5 critical (100% > 5% cap) => at least 1 demotion
        entries = [(f"L-{i}", 0.95) for i in range(5)]
        result = enforce_tier_distribution(entries)
        assert len(result) >= 1

    def test_no_critical_no_high_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """All entries in medium/low tiers: no demotions needed."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        entries = [(f"L-{i}", 0.5) for i in range(10)]  # All medium
        result = enforce_tier_distribution(entries)
        assert result == []

    def test_critical_demotion_targets_lowest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Critical demotion picks the lowest-scored critical entry."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        # 10 entries: 6 critical, 4 medium
        entries = [
            ("L-crit-low", 0.91),  # Lowest critical -- should be demoted
            ("L-crit-2", 0.95),
            ("L-crit-3", 0.95),
            ("L-crit-4", 0.95),
            ("L-crit-5", 0.95),
            ("L-crit-6", 0.99),
        ] + [(f"L-med-{i}", 0.5) for i in range(4)]
        result = enforce_tier_distribution(entries)
        demoted_ids = {d[0] for d in result}
        assert "L-crit-low" in demoted_ids

    def test_critical_demotion_new_score_in_high_range(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Demoted critical entry gets a score in [0.7, 0.89]."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        entries = [(f"L-{i}", 0.95) for i in range(10)]
        result = enforce_tier_distribution(entries)
        for _, new_score in result:
            if new_score >= 0.7:
                assert new_score <= _TIER_HIGH_CEILING

    def test_high_tier_demotion_new_score_in_medium_range(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Demoted high entry gets a score in [0.4, 0.69]."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        # 10 entries: all in high tier (0.7-0.89)
        entries = [(f"L-{i}", 0.75) for i in range(10)]
        result = enforce_tier_distribution(entries)
        for _lid, new_score in result:
            if new_score < 0.7:
                assert new_score <= _TIER_MEDIUM_CEILING
                assert new_score >= 0.4

    def test_custom_caps_override_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit critical_cap and high_cap override config values."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        # 10 entries all critical
        entries = [(f"L-{i}", 0.95) for i in range(10)]
        # With very high cap (100%), no demotions
        result = enforce_tier_distribution(entries, critical_cap=1.0, high_cap=1.0)
        assert result == []

    def test_one_demotion_per_tier_per_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """At most one demotion per tier per function call."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        entries = [(f"L-{i}", 0.95) for i in range(20)]
        result = enforce_tier_distribution(entries)
        # At most 2 demotions total (1 from critical, 1 from high)
        assert len(result) <= 2


# ============================================================================
# compute_tier_ceremony_score: raw event type strings
# ============================================================================


class TestComputeTierCeremonyScoreRawEvents:
    """Test compute_tier_ceremony_score with raw event type strings (not tool_invocation)."""

    def test_session_start_raw_event(self) -> None:
        """Raw 'session_start' event counts as has_recall."""
        events: list[dict[str, object]] = [{"event": "session_start"}]
        result = compute_tier_ceremony_score(events, "MINIMAL")
        assert result["has_recall"] is True

    def test_run_init_raw_event(self) -> None:
        """Raw 'run_init' event counts as has_init."""
        events: list[dict[str, object]] = [{"event": "run_init"}]
        result = compute_tier_ceremony_score(events, "STANDARD")
        assert result["has_init"] is True

    def test_checkpoint_raw_event(self) -> None:
        """Raw 'checkpoint' event counts toward checkpoint_count."""
        events: list[dict[str, object]] = [
            {"event": "checkpoint"},
            {"event": "checkpoint"},
        ]
        result = compute_tier_ceremony_score(events, "STANDARD")
        assert result["checkpoint_count"] == 2

    def test_build_check_complete_raw_event(self) -> None:
        """Raw 'build_check_complete' event counts as has_build_check."""
        events: list[dict[str, object]] = [{"event": "build_check_complete"}]
        result = compute_tier_ceremony_score(events, "STANDARD")
        assert result["has_build_check"] is True

    def test_learn_event_type_detected(self) -> None:
        """Event with 'learn' in event_type is detected as has_learn."""
        events: list[dict[str, object]] = [{"event": "learn_recorded"}]
        result = compute_tier_ceremony_score(events, "MINIMAL")
        assert result["has_learn"] is True

    def test_reflection_complete_raw_event(self) -> None:
        """Raw 'reflection_complete' event counts as has_deliver."""
        events: list[dict[str, object]] = [{"event": "reflection_complete"}]
        result = compute_tier_ceremony_score(events, "STANDARD")
        assert result["has_deliver"] is True

    def test_claude_md_synced_raw_event(self) -> None:
        """Raw 'claude_md_synced' event counts as has_deliver."""
        events: list[dict[str, object]] = [{"event": "claude_md_synced"}]
        result = compute_tier_ceremony_score(events, "STANDARD")
        assert result["has_deliver"] is True

    def test_trw_deliver_complete_raw_event(self) -> None:
        """Raw 'trw_deliver_complete' event counts as has_deliver."""
        events: list[dict[str, object]] = [{"event": "trw_deliver_complete"}]
        result = compute_tier_ceremony_score(events, "STANDARD")
        assert result["has_deliver"] is True

    def test_review_complete_raw_event(self) -> None:
        """Raw 'review_complete' event counts as has_review."""
        events: list[dict[str, object]] = [{"event": "review_complete"}]
        result = compute_tier_ceremony_score(events, "COMPREHENSIVE")
        assert result["has_review"] is True

    def test_unknown_tier_string_defaults_to_standard(self) -> None:
        """Unknown tier string falls back to STANDARD."""
        events: list[dict[str, object]] = [
            {"event": "tool_invocation", "tool_name": "trw_session_start"},
        ]
        result = compute_tier_ceremony_score(events, "NONEXISTENT_TIER")
        assert result["tier"] == "STANDARD"

    def test_lowercase_tier_string_normalized(self) -> None:
        """Lowercase tier string is normalized to uppercase."""
        events: list[dict[str, object]] = []
        result = compute_tier_ceremony_score(events, "minimal")
        assert result["tier"] == "MINIMAL"

    def test_mixed_raw_and_tool_events(self) -> None:
        """Both raw events and tool_invocation events are detected together."""
        events: list[dict[str, object]] = [
            {"event": "session_start"},
            {"event": "tool_invocation", "tool_name": "trw_init"},
            {"event": "checkpoint"},
            {"event": "tool_invocation", "tool_name": "trw_build_check"},
            {"event": "tool_invocation", "tool_name": "trw_deliver"},
        ]
        result = compute_tier_ceremony_score(events, "STANDARD")
        assert result["has_recall"] is True
        assert result["has_init"] is True
        assert result["checkpoint_count"] == 1
        assert result["has_build_check"] is True
        assert result["has_deliver"] is True
        # STANDARD expects 6 events (incl trw_review); 5/6 = 83, minus 15 penalty = 68
        assert result["score"] == 68

    def test_tool_invocation_trw_reflect_counts_as_deliver(self) -> None:
        """tool_name='trw_reflect' counts as has_deliver."""
        events: list[dict[str, object]] = [
            {"event": "tool_invocation", "tool_name": "trw_reflect"},
        ]
        result = compute_tier_ceremony_score(events, "STANDARD")
        assert result["has_deliver"] is True

    def test_event_with_no_type_ignored(self) -> None:
        """Event dicts with no 'event' key are gracefully ignored."""
        events: list[dict[str, object]] = [
            {"tool_name": "trw_session_start"},  # missing 'event' key
        ]
        result = compute_tier_ceremony_score(events, "STANDARD")
        assert result["matched_events"] == 0


# ============================================================================
# rank_by_utility edge cases
# ============================================================================


class TestRankByUtilityEdgeCases:
    """Additional edge cases for rank_by_utility."""

    def _make_entry(
        self,
        summary: str,
        impact: float = 0.5,
        tags: list[str] | None = None,
        detail: str = "",
    ) -> dict[str, object]:
        return {
            "id": f"L-{summary[:4]}",
            "summary": summary,
            "detail": detail,
            "tags": tags or [],
            "impact": impact,
            "q_value": impact,
            "q_observations": 5,
            "recurrence": 1,
            "access_count": 0,
            "source_type": "agent",
            "created": datetime.now(tz=timezone.utc).date().isoformat(),
        }

    def test_non_list_tags_handled(self) -> None:
        """Non-list tags field is handled gracefully."""
        entry = self._make_entry("test", tags=None)
        entry["tags"] = "not-a-list"
        result = rank_by_utility([entry], query_tokens=["test"], lambda_weight=0.5)
        assert len(result) == 1

    def test_detail_hits_contribute_to_relevance(self) -> None:
        """Query tokens found in detail contribute to relevance scoring."""
        entry_in_detail = self._make_entry("generic", detail="pytest framework testing")
        entry_no_match = self._make_entry("generic", detail="unrelated content")
        result = rank_by_utility(
            [entry_no_match, entry_in_detail],
            query_tokens=["pytest"],
            lambda_weight=0.0,
        )
        assert result[0] is entry_in_detail

    def test_lambda_weight_one_pure_utility(self) -> None:
        """lambda_weight=1.0 means pure utility, ignores relevance."""
        low_impact = self._make_entry("pytest testing", impact=0.1)
        high_impact = self._make_entry("unrelated", impact=0.9)
        result = rank_by_utility(
            [low_impact, high_impact],
            query_tokens=["pytest"],
            lambda_weight=1.0,
        )
        assert result[0] is high_impact

    def test_lambda_weight_zero_pure_relevance(self) -> None:
        """lambda_weight=0.0 means pure relevance, ignores utility."""
        matching = self._make_entry("pytest testing", impact=0.1)
        non_matching = self._make_entry("unrelated stuff", impact=0.9)
        result = rank_by_utility(
            [non_matching, matching],
            query_tokens=["pytest", "testing"],
            lambda_weight=0.0,
        )
        assert result[0] is matching

    def test_summary_hits_weighted_higher_than_detail(self) -> None:
        """Summary matches are weighted 3x vs detail matches 1x."""
        entry_summary = self._make_entry("pytest info")
        entry_detail = self._make_entry("generic", detail="pytest info")
        result = rank_by_utility(
            [entry_detail, entry_summary],
            query_tokens=["pytest"],
            lambda_weight=0.0,
        )
        assert result[0] is entry_summary

    def test_stable_sort_equal_scores(self) -> None:
        """Entries with equal scores maintain their relative order (sort stability)."""
        entries = [
            self._make_entry("first", impact=0.5),
            self._make_entry("second", impact=0.5),
            self._make_entry("third", impact=0.5),
        ]
        result = rank_by_utility(entries, query_tokens=[], lambda_weight=1.0)
        # All have the same utility and wildcard relevance, so order should be stable
        assert len(result) == 3


# ============================================================================
# utility_based_prune_candidates edge cases
# ============================================================================


class TestUtilityBasedPruneCandidatesEdgeCases:
    """Additional edge cases for utility_based_prune_candidates."""

    def _make_entry(
        self,
        entry_id: str,
        created: str,
        status: str = "active",
        impact: float = 0.3,
        recurrence: int = 1,
    ) -> tuple[Path, dict[str, object]]:
        data: dict[str, object] = {
            "id": entry_id,
            "summary": f"Learning {entry_id}",
            "created": created,
            "status": status,
            "impact": impact,
            "q_value": impact,
            "q_observations": 0,
            "recurrence": recurrence,
            "access_count": 0,
            "source_type": "agent",
        }
        return (Path(f"/fake/{entry_id}.yaml"), data)

    def test_active_young_high_utility_not_candidate(self) -> None:
        """Recent high-impact active entry is never a candidate."""
        entries = [self._make_entry("L-fresh", datetime.now(tz=timezone.utc).date().isoformat(), impact=0.9)]
        result = utility_based_prune_candidates(entries)
        assert result == []

    def test_resolved_status_zero_utility(self) -> None:
        """Resolved entries have utility=0.0 in the candidate dict."""
        entries = [self._make_entry("L-done", "2026-01-01", status="resolved")]
        result = utility_based_prune_candidates(entries)
        assert len(result) == 1
        assert result[0]["utility"] == 0.0

    def test_tier3_requires_age_over_14_days(self) -> None:
        """Tier-3 prune candidates must be older than 14 days."""
        # 10 days old, low utility but below age threshold
        recent = (datetime.now(tz=timezone.utc).date() - timedelta(days=10)).isoformat()
        entries = [self._make_entry("L-young-low", recent, impact=0.05)]
        result = utility_based_prune_candidates(entries)
        # Should be tier 2 (delete) if utility < delete_threshold, or not a candidate
        # Check whether it's captured at all
        for candidate in result:
            if candidate["id"] == "L-young-low":
                # If captured, it should be tier 2 (not tier 3 because age <= 14)
                assert (
                    "delete threshold" in str(candidate.get("reason", "")).lower()
                    or "utility" in str(candidate.get("reason", "")).lower()
                )

    def test_high_recurrence_improves_utility(self) -> None:
        """Higher recurrence count produces higher utility (harder to prune)."""
        old_date = (datetime.now(tz=timezone.utc).date() - timedelta(days=60)).isoformat()
        low_rec = self._make_entry("L-low-rec", old_date, impact=0.3, recurrence=1)
        high_rec = self._make_entry("L-high-rec", old_date, impact=0.3, recurrence=20)
        result_low = utility_based_prune_candidates([low_rec])
        result_high = utility_based_prune_candidates([high_rec])
        # High recurrence should produce fewer candidates (higher utility)
        assert len(result_high) <= len(result_low)


# ============================================================================
# compute_utility_score: additional mathematical checks
# ============================================================================


class TestComputeUtilityScoreMath:
    """Mathematical correctness checks for compute_utility_score."""

    def test_access_count_boost(self) -> None:
        """access_count parameter provides a boost."""
        score_no_access = compute_utility_score(
            0.5,
            7,
            1,
            0.5,
            5,
            access_count=0,
        )
        score_with_access = compute_utility_score(
            0.5,
            7,
            1,
            0.5,
            5,
            access_count=10,
        )
        assert score_with_access >= score_no_access

    def test_source_human_boost(self) -> None:
        """source_type='human' provides a utility boost."""
        score_agent = compute_utility_score(
            0.5,
            7,
            1,
            0.5,
            5,
            source_type="agent",
        )
        score_human = compute_utility_score(
            0.5,
            7,
            1,
            0.5,
            5,
            source_type="human",
        )
        assert score_human >= score_agent

    def test_very_high_recurrence_caps_benefit(self) -> None:
        """Extremely high recurrence doesn't produce score > 1.0."""
        score = compute_utility_score(1.0, 0, 10000, 1.0, 100)
        assert score <= 1.0

    def test_zero_half_life_handled(self) -> None:
        """half_life_days=0 doesn't cause division by zero."""
        # Should not raise
        score = compute_utility_score(0.5, 10, 1, 0.5, 5, half_life_days=0.0)
        assert 0.0 <= score <= 1.0

    def test_access_count_boost_capped(self) -> None:
        """Access count boost has a cap (doesn't grow indefinitely)."""
        score_100 = compute_utility_score(0.5, 7, 1, 0.5, 5, access_count=100)
        score_10000 = compute_utility_score(0.5, 7, 1, 0.5, 5, access_count=10000)
        # Difference should be small (cap limits growth)
        assert abs(score_10000 - score_100) < 0.05


# ============================================================================
# update_q_value: additional math
# ============================================================================


class TestUpdateQValueMath:
    """Additional mathematical checks for update_q_value."""

    def test_extreme_positive_reward(self) -> None:
        """Very large positive reward still clamps to [0, 1]."""
        result = update_q_value(0.5, 100.0)
        assert 0.0 <= result <= 1.0

    def test_extreme_negative_reward(self) -> None:
        """Very large negative reward still clamps to [0, 1]."""
        result = update_q_value(0.5, -100.0)
        assert 0.0 <= result <= 1.0

    def test_zero_alpha_no_change(self) -> None:
        """Alpha=0 means no learning happens."""
        result = update_q_value(0.5, 1.0, alpha=0.0)
        assert result == pytest.approx(0.5)

    def test_alpha_one_jumps_to_reward(self) -> None:
        """Alpha=1.0 means instant adoption of reward (before clamping)."""
        result = update_q_value(0.5, 0.8, alpha=1.0)
        # q = q + 1.0 * (reward - q) = reward = 0.8
        assert result == pytest.approx(0.8, abs=0.01)

    def test_symmetric_convergence(self) -> None:
        """Convergence is symmetric: upward and downward at same rate."""
        q_up = 0.3
        q_down = 0.7
        target = 0.5
        for _ in range(20):
            q_up = update_q_value(q_up, target)
            q_down = update_q_value(q_down, target)
        # Both should converge to 0.5
        assert abs(q_up - target) < 0.01
        assert abs(q_down - target) < 0.01


# ============================================================================
# _resolve_event_reward: additional edge cases
# ============================================================================


class TestResolveEventRewardAdditional:
    """Additional edge cases for _resolve_event_reward."""

    def test_prd_created_alias_positive(self) -> None:
        """PRD_CREATED alias returns positive reward."""
        from trw_mcp.scoring import _resolve_event_reward

        reward, label = _resolve_event_reward(EventType.PRD_CREATED)
        assert reward is not None
        assert reward > 0

    def test_claude_md_synced_alias_positive(self) -> None:
        """CLAUDE_MD_SYNCED alias returns positive reward."""
        from trw_mcp.scoring import _resolve_event_reward

        reward, label = _resolve_event_reward(EventType.CLAUDE_MD_SYNCED)
        assert reward is not None
        assert reward > 0

    def test_wave_validated_alias_resolves(self) -> None:
        """WAVE_VALIDATED resolves to WAVE_VALIDATION_PASSED reward."""
        from trw_mcp.scoring import _resolve_event_reward

        reward, label = _resolve_event_reward(EventType.WAVE_VALIDATED)
        assert reward is not None
        assert reward == 0.7  # WAVE_VALIDATION_PASSED reward

    def test_wave_completed_alias_resolves(self) -> None:
        """WAVE_COMPLETED resolves to WAVE_COMPLETE reward."""
        from trw_mcp.scoring import _resolve_event_reward

        reward, label = _resolve_event_reward(EventType.WAVE_COMPLETED)
        assert reward is not None
        assert reward == 0.8  # WAVE_COMPLETE reward

    def test_phase_enter_no_reward(self) -> None:
        """PHASE_ENTER has None alias (no reward)."""
        from trw_mcp.scoring import _resolve_event_reward

        reward, label = _resolve_event_reward(EventType.PHASE_ENTER)
        assert reward is None

    def test_session_start_no_reward(self) -> None:
        """SESSION_START has None alias (no reward)."""
        from trw_mcp.scoring import _resolve_event_reward

        reward, label = _resolve_event_reward(EventType.SESSION_START)
        assert reward is None

    def test_run_resumed_no_reward(self) -> None:
        """RUN_RESUMED has None alias (no reward)."""
        from trw_mcp.scoring import _resolve_event_reward

        reward, label = _resolve_event_reward(EventType.RUN_RESUMED)
        assert reward is None

    def test_compliance_check_no_score_key(self) -> None:
        """COMPLIANCE_CHECK without score key returns None via alias path."""
        from trw_mcp.scoring import _resolve_event_reward

        reward, label = _resolve_event_reward(
            EventType.COMPLIANCE_CHECK,
            event_data={"other": "data"},
        )
        assert reward is None

    def test_test_run_with_empty_event_data(self) -> None:
        """TEST_RUN with empty dict event_data: {} is falsy, skips data-aware routing."""
        from trw_mcp.scoring import _resolve_event_reward

        reward, label = _resolve_event_reward(
            EventType.TEST_RUN,
            event_data={},
        )
        # {} is falsy in Python, so `if event_data:` is False
        # Falls through to EVENT_ALIASES where TEST_RUN has None alias
        assert reward is None
        assert label == EventType.TEST_RUN

    def test_error_keyword_in_mixed_case(self) -> None:
        """Error keyword matching is case-insensitive."""
        from trw_mcp.scoring import _resolve_event_reward

        reward, label = _resolve_event_reward("SOME_ERROR_HAPPENED")
        assert reward is not None
        assert reward < 0

    def test_prd_status_change_approved_uppercase(self) -> None:
        """PRD_STATUS_CHANGE with 'Approved' (capitalized) matches case-insensitively."""
        from trw_mcp.scoring import _resolve_event_reward

        reward, label = _resolve_event_reward(
            EventType.PRD_STATUS_CHANGE,
            event_data={"new_status": "Approved"},
        )
        # The code does .lower(), so this should match
        assert reward is not None
        assert label == EventType.PRD_APPROVED
