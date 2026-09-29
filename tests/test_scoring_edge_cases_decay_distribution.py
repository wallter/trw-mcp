"""Edge-case tests for impact decay and tier distribution behavior."""

from __future__ import annotations

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.scoring import (
    _TIER_HIGH_CEILING,
    _TIER_MEDIUM_CEILING,
    enforce_tier_distribution,
)


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
