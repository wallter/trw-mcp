"""Edge-case tests for impact decay and tier distribution behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.scoring import (
    _IMPACT_DECAY_FLOOR,
    _TIER_HIGH_CEILING,
    _TIER_MEDIUM_CEILING,
    apply_impact_decay,
    enforce_tier_distribution,
)


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
        old_date = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        entry: dict[str, object] = {"impact": 0.9, "created": old_date}
        entries_short = [dict(entry)]
        entries_long = [dict(entry)]
        apply_impact_decay(entries_short, half_life_days=7)
        apply_impact_decay(entries_long, half_life_days=300)
        short_impact = float(str(entries_short[0]["impact"]))
        long_impact = float(str(entries_long[0]["impact"]))
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
        for entry in entries:
            assert float(str(entry["impact"])) < 0.9

    def test_modifies_in_place_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """apply_impact_decay modifies entries in-place and returns None."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        old = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        entries: list[dict[str, object]] = [{"impact": 0.9, "created": old}]
        result = apply_impact_decay(entries)
        assert result is None
        assert float(str(entries[0]["impact"])) < 0.9


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
        entries = [(f"L-{i}", 0.95) for i in range(5)]
        result = enforce_tier_distribution(entries)
        assert len(result) >= 1

    def test_no_critical_no_high_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """All entries in medium/low tiers: no demotions needed."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        entries = [(f"L-{i}", 0.5) for i in range(10)]
        result = enforce_tier_distribution(entries)
        assert result == []

    def test_critical_demotion_targets_lowest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Critical demotion picks the lowest-scored critical entry."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        entries = [
            ("L-crit-low", 0.91),
            ("L-crit-2", 0.95),
            ("L-crit-3", 0.95),
            ("L-crit-4", 0.95),
            ("L-crit-5", 0.95),
            ("L-crit-6", 0.99),
        ] + [(f"L-med-{i}", 0.5) for i in range(4)]
        result = enforce_tier_distribution(entries)
        demoted_ids = {entry_id for entry_id, _ in result}
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
        entries = [(f"L-{i}", 0.95) for i in range(10)]
        result = enforce_tier_distribution(entries, critical_cap=1.0, high_cap=1.0)
        assert result == []

    def test_one_demotion_per_tier_per_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """At most one demotion per tier per function call."""
        cfg = TRWConfig()
        monkeypatch.setattr("trw_mcp.scoring._decay.get_config", lambda: cfg)
        entries = [(f"L-{i}", 0.95) for i in range(20)]
        result = enforce_tier_distribution(entries)
        assert len(result) <= 2
