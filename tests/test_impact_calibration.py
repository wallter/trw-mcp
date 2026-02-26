"""Tests for PRD-CORE-034 impact score calibration features.

Covers:
- enforce_tier_distribution: forced distribution cap enforcement
- apply_time_decay: Ebbinghaus decay for impact scores
- apply_impact_decay: batch impact decay for stale learnings (FR03)
- Config defaults: outcome_window = 480, impact_high_threshold_pct, impact_decay_half_life_days
- Integration: wiring into trw_learn (demotion side effects)
- Integration: decay applied during recall ranking via _entry_utility
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from trw_mcp.scoring import apply_impact_decay, apply_time_decay, enforce_tier_distribution


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(days_ago: int = 0) -> datetime:
    """Return a UTC datetime *days_ago* days in the past."""
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


# ---------------------------------------------------------------------------
# enforce_tier_distribution
# ---------------------------------------------------------------------------


class TestEnforceTierDistribution:
    """Forced distribution cap enforcement (PRD-CORE-034-FR01)."""

    # --- Edge / boundary cases ---

    def test_empty_list_returns_no_demotions(self) -> None:
        result = enforce_tier_distribution([])
        assert result == []

    def test_single_entry_no_demotion(self) -> None:
        """Single critical entry: total < 5, no enforcement."""
        result = enforce_tier_distribution([("L-001", 0.95)])
        assert result == []

    def test_four_entries_no_enforcement(self) -> None:
        """Fewer than 5 total entries: caps are never triggered."""
        entries = [("L-%03d" % i, 0.95) for i in range(4)]
        result = enforce_tier_distribution(entries)
        assert result == []

    def test_exactly_five_entries_enforcement_starts(self) -> None:
        """With 5 entries, cap enforcement is active."""
        # 5 critical entries = 100% critical, well above 5% cap
        entries = [("L-%03d" % i, 0.95) for i in range(5)]
        result = enforce_tier_distribution(entries)
        assert len(result) >= 1

    # --- Within-cap: no demotions ---

    def test_within_critical_cap_no_demotion(self) -> None:
        """Critical tier at exactly cap: no demotions."""
        # 1 critical out of 20 = 5% == cap, no demotion needed
        entries: list[tuple[str, float]] = [("L-crit", 0.95)]
        entries += [("L-med-%02d" % i, 0.5) for i in range(19)]
        result = enforce_tier_distribution(entries)
        assert result == []

    def test_within_high_cap_no_demotion(self) -> None:
        """High tier at exactly cap: no demotions."""
        # 4 high out of 20 = 20% == cap, no demotion needed
        entries: list[tuple[str, float]] = [("L-high-%d" % i, 0.75) for i in range(4)]
        entries += [("L-med-%02d" % i, 0.5) for i in range(16)]
        result = enforce_tier_distribution(entries)
        assert result == []

    # --- Over-cap: demotions triggered ---

    def test_critical_tier_over_cap_demotes_one(self) -> None:
        """Critical tier at >5%: lowest critical entry demoted to high."""
        # 2 critical out of 10 = 20% > 5% cap
        entries: list[tuple[str, float]] = [
            ("L-crit-low", 0.91),
            ("L-crit-high", 0.99),
        ]
        entries += [("L-med-%d" % i, 0.5) for i in range(8)]
        result = enforce_tier_distribution(entries)
        assert len(result) == 1
        demoted_id, new_score = result[0]
        assert demoted_id == "L-crit-low"  # lowest critical demoted
        assert 0.7 <= new_score <= 0.89

    def test_high_tier_over_cap_demotes_one(self) -> None:
        """High tier at >20%: lowest high entry demoted to medium."""
        # 5 high out of 10 = 50% > 20% cap
        entries: list[tuple[str, float]] = [
            ("L-high-low", 0.71),
            ("L-high-mid", 0.75),
            ("L-high-high", 0.85),
            ("L-high-v2", 0.80),
            ("L-high-v3", 0.73),
        ]
        entries += [("L-med-%d" % i, 0.5) for i in range(5)]
        result = enforce_tier_distribution(entries)
        demoted_ids = [d[0] for d in result]
        assert "L-high-low" in demoted_ids  # lowest high is 0.71
        for _, new_score in result:
            assert 0.4 <= new_score <= 0.69

    def test_both_tiers_over_cap_demotes_from_both(self) -> None:
        """Both critical and high over cap: both get one demotion each."""
        # 2 critical (20% > 5%), 5 high (50% > 20%), out of 10 total
        entries: list[tuple[str, float]] = [
            ("L-crit-1", 0.92),
            ("L-crit-2", 0.96),
            ("L-high-1", 0.71),
            ("L-high-2", 0.75),
            ("L-high-3", 0.82),
            ("L-high-4", 0.85),
            ("L-high-5", 0.78),
        ]
        entries += [("L-med-%d" % i, 0.5) for i in range(3)]
        result = enforce_tier_distribution(entries)
        # At least one critical demoted to high
        crit_demotions = [(i, s) for i, s in result if 0.7 <= s <= 0.89]
        assert len(crit_demotions) >= 1

    def test_demotion_selects_lowest_critical(self) -> None:
        """Lowest-scored critical entry is the demotion victim."""
        entries: list[tuple[str, float]] = [
            ("L-lowest", 0.90),
            ("L-middle", 0.95),
            ("L-highest", 0.99),
        ]
        entries += [("L-med-%d" % i, 0.5) for i in range(17)]
        result = enforce_tier_distribution(entries)
        ids_demoted = [d[0] for d in result]
        assert "L-lowest" in ids_demoted

    def test_demotion_selects_lowest_high(self) -> None:
        """Lowest-scored high entry is the demotion victim."""
        entries: list[tuple[str, float]] = [
            ("L-hlow", 0.70),
            ("L-hmid", 0.78),
            ("L-hhigh", 0.88),
        ]
        entries += [("L-med-%d" % i, 0.5) for i in range(7)]
        result = enforce_tier_distribution(entries)
        ids_demoted = [d[0] for d in result]
        assert "L-hlow" in ids_demoted

    # --- New score bounds ---

    def test_demoted_critical_score_in_high_range(self) -> None:
        """Critical demotion result is in [0.7, 0.89]."""
        entries = [("L-crit", 0.95)] + [("L-m-%d" % i, 0.5) for i in range(19)]
        result = enforce_tier_distribution(entries)
        for _, new_score in result:
            if new_score <= 0.89:  # was critical, now high
                assert 0.7 <= new_score <= 0.89

    def test_demoted_high_score_in_medium_range(self) -> None:
        """High demotion result is in [0.4, 0.69]."""
        entries = [("L-h-%d" % i, 0.75) for i in range(5)]
        entries += [("L-m-%d" % i, 0.5) for i in range(5)]
        result = enforce_tier_distribution(entries)
        for _, new_score in result:
            if new_score <= 0.69:  # was high, now medium
                assert 0.4 <= new_score <= 0.69

    # --- Custom caps ---

    def test_custom_critical_cap(self) -> None:
        """Custom critical_cap overrides config default."""
        # 1 critical out of 10 = 10% > 5% default, but <= 15% custom
        entries = [("L-crit", 0.95)] + [("L-m-%d" % i, 0.5) for i in range(9)]
        # With default cap (5%): 10% > 5% → demotion
        default_result = enforce_tier_distribution(entries)
        assert len(default_result) >= 1
        # With cap=0.15: 10% <= 15% → no demotion
        custom_result = enforce_tier_distribution(entries, critical_cap=0.15)
        assert custom_result == []

    def test_custom_high_cap(self) -> None:
        """Custom high_cap overrides config default."""
        # 3 high out of 10 = 30% > 20% default, but <= 40% custom
        entries = [("L-h-%d" % i, 0.75) for i in range(3)]
        entries += [("L-m-%d" % i, 0.5) for i in range(7)]
        default_result = enforce_tier_distribution(entries)
        assert len(default_result) >= 1
        custom_result = enforce_tier_distribution(entries, high_cap=0.40)
        assert custom_result == []

    # --- All-same-tier edge cases ---

    def test_all_medium_no_demotion(self) -> None:
        """All entries in medium tier: no critical/high entries → no demotions."""
        entries = [("L-%d" % i, 0.5) for i in range(20)]
        result = enforce_tier_distribution(entries)
        assert result == []

    def test_all_low_no_demotion(self) -> None:
        """All entries in low tier: no demotions needed."""
        entries = [("L-%d" % i, 0.2) for i in range(20)]
        result = enforce_tier_distribution(entries)
        assert result == []

    def test_return_is_list_of_tuples(self) -> None:
        """Returns list of (id, float) tuples."""
        entries = [("L-crit", 0.95)] + [("L-m-%d" % i, 0.5) for i in range(19)]
        result = enforce_tier_distribution(entries)
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, tuple)
            assert len(item) == 2
            lid, score = item
            assert isinstance(lid, str)
            assert isinstance(score, float)


# ---------------------------------------------------------------------------
# apply_time_decay
# ---------------------------------------------------------------------------


class TestApplyTimeDecay:
    """Ebbinghaus time decay for impact scores (PRD-CORE-034-FR03)."""

    def test_zero_days_no_decay(self) -> None:
        """Brand-new learning (0 days): decay_factor = 1.0."""
        result = apply_time_decay(0.8, _utc(0))
        assert result == pytest.approx(0.8, abs=0.01)

    def test_one_year_30pct_decay(self) -> None:
        """365-day-old learning: decay_factor = max(0.3, 1.0 - 0.3) = 0.7."""
        result = apply_time_decay(1.0, _utc(365))
        assert result == pytest.approx(0.7, abs=0.01)

    def test_two_year_floored_at_30pct(self) -> None:
        """730-day-old learning: factor = max(0.3, 1.0 - 0.6) = 0.4 → 0.4."""
        result = apply_time_decay(1.0, _utc(730))
        # decay_factor = max(0.3, 1.0 - (730/365)*0.3) = max(0.3, 0.4) = 0.4
        assert result == pytest.approx(0.4, abs=0.01)

    def test_extreme_old_floored(self) -> None:
        """Very old learning (10 years): decay_factor floored at 0.3."""
        result = apply_time_decay(1.0, _utc(3650))
        assert result == pytest.approx(0.3, abs=0.01)

    def test_half_year_partial_decay(self) -> None:
        """6-month-old learning: ~15% decay."""
        result = apply_time_decay(1.0, _utc(182))
        expected_factor = max(0.3, 1.0 - (182 / 365) * 0.3)
        assert result == pytest.approx(expected_factor, abs=0.01)

    def test_output_clamped_high(self) -> None:
        """Result never exceeds 1.0."""
        result = apply_time_decay(1.0, _utc(0))
        assert result <= 1.0

    def test_output_clamped_low(self) -> None:
        """Result never goes below 0.0."""
        result = apply_time_decay(0.0, _utc(3650))
        assert result >= 0.0

    def test_high_impact_decays_more_in_absolute(self) -> None:
        """Higher impact scores lose more in absolute terms."""
        decay_high = apply_time_decay(0.9, _utc(365))
        decay_low = apply_time_decay(0.3, _utc(365))
        # 0.9 * 0.7 = 0.63,  0.3 * 0.7 = 0.21
        assert (0.9 - decay_high) > (0.3 - decay_low)

    def test_monotonic_decay_with_age(self) -> None:
        """Older entries have lower effective impact (monotonically)."""
        scores = [apply_time_decay(0.8, _utc(d)) for d in [0, 90, 180, 365, 730]]
        for i in range(1, len(scores)):
            assert scores[i] <= scores[i - 1]

    def test_naive_datetime_treated_as_utc(self) -> None:
        """Naive datetimes are treated as UTC (no error)."""
        naive_dt = datetime(2025, 1, 1, 12, 0, 0)  # no tzinfo
        result = apply_time_decay(0.8, naive_dt)
        assert 0.0 <= result <= 1.0

    def test_aware_datetime_in_other_tz(self) -> None:
        """Timezone-aware datetimes in non-UTC zones are handled correctly."""
        from datetime import timezone as _tz
        import datetime as dt_mod
        est = _tz(timedelta(hours=-5))
        past = datetime(2025, 2, 22, 8, 0, 0, tzinfo=est)
        result = apply_time_decay(0.8, past)
        assert 0.0 <= result <= 0.8


# ---------------------------------------------------------------------------
# Integration: enforce_tier_distribution wired into trw_learn
# ---------------------------------------------------------------------------


class TestTrwLearnForcedDistributionWiring:
    """Verify enforce_tier_distribution is called and demotions persist."""

    @pytest.fixture(autouse=True)
    def _isolate_project_root(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

    def _write_entry(self, entries_dir: Path, fname: str, impact: float, status: str = "active") -> None:
        entries_dir.mkdir(parents=True, exist_ok=True)
        (entries_dir / fname).write_text(
            f"id: {fname}\nimpact: {impact}\nstatus: {status}\n"
        )

    def _get_tools(self) -> dict[str, Any]:
        from fastmcp import FastMCP
        from trw_mcp.tools.learning import register_learning_tools
        srv = FastMCP("test")
        register_learning_tools(srv)
        return {t.name: t for t in srv._tool_manager._tools.values()}

    def _entries_dir(self, root: Path) -> Path:
        from trw_mcp.models.config import TRWConfig
        cfg = TRWConfig()
        return root / cfg.trw_dir / cfg.learnings_dir / cfg.entries_dir

    def test_demotion_persisted_to_disk(self, tmp_path: Path) -> None:
        """Demoted entries have their impact scores updated on disk."""
        tools = self._get_tools()
        entries_dir = self._entries_dir(tmp_path)
        # 10 critical entries at 0.95: critical tier = 100% >> 5% cap
        for i in range(10):
            self._write_entry(entries_dir, f"entry_{i}.yaml", 0.95)

        result = tools["trw_learn"].fn(
            summary="New critical learning",
            detail="Detail",
            impact=0.95,
        )
        assert result["status"] == "recorded"
        # At least one entry should have been demoted (impact != 0.95)
        from trw_mcp.state.persistence import FileStateReader
        reader = FileStateReader()
        impacts = []
        for yaml_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(yaml_file)
            impacts.append(float(str(data.get("impact", 0.5))))
        # Not all entries can be at 0.95 — at least one demotion happened
        assert min(impacts) < 0.9

    def test_no_demotion_when_disabled(self, tmp_path: Path) -> None:
        """When impact_forced_distribution_enabled=False, no demotions occur."""
        import trw_mcp.tools.learning as learning_mod
        from trw_mcp.models.config import TRWConfig
        disabled_cfg = TRWConfig().model_copy(update={"impact_forced_distribution_enabled": False})
        with patch.object(learning_mod, "_config", disabled_cfg):
            tools = self._get_tools()
            entries_dir = self._entries_dir(tmp_path)
            for i in range(10):
                self._write_entry(entries_dir, f"entry_{i}.yaml", 0.95)

            result = tools["trw_learn"].fn(
                summary="Critical",
                detail="Detail",
                impact=0.95,
            )
        assert result["distribution_warning"] == ""

    def test_demotion_warning_contains_tier_name(self, tmp_path: Path) -> None:
        """Distribution warning message names the affected tier."""
        tools = self._get_tools()
        entries_dir = self._entries_dir(tmp_path)
        for i in range(10):
            self._write_entry(entries_dir, f"entry_{i}.yaml", 0.95)

        result = tools["trw_learn"].fn(
            summary="Critical learning",
            detail="Very important",
            impact=0.95,
        )
        warning = result["distribution_warning"]
        assert warning != ""
        # Warning should mention the tier and the cap concept
        assert "critical" in warning or "high" in warning

    def test_no_demotion_below_impact_threshold(self, tmp_path: Path) -> None:
        """Low-impact learnings (< 0.7) don't trigger distribution enforcement."""
        tools = self._get_tools()
        entries_dir = self._entries_dir(tmp_path)
        for i in range(10):
            self._write_entry(entries_dir, f"entry_{i}.yaml", 0.95)

        result = tools["trw_learn"].fn(
            summary="Low impact",
            detail="Not important",
            impact=0.5,
        )
        assert result["distribution_warning"] == ""


# ---------------------------------------------------------------------------
# Integration: apply_time_decay wired into recall ranking
# ---------------------------------------------------------------------------


class TestRecallRankingDecayIntegration:
    """Verify older learnings rank lower in recall due to time decay."""

    def test_new_entry_ranks_higher_than_old_entry(self) -> None:
        """A brand-new entry ranks higher than a 2-year-old entry (all else equal)."""
        from trw_mcp.scoring import rank_by_utility

        fresh_entry: dict[str, object] = {
            "id": "L-fresh",
            "summary": "fresh learning about testing",
            "detail": "fresh detail",
            "tags": [],
            "impact": 0.8,
            "q_value": 0.8,
            "q_observations": 5,
            "recurrence": 1,
            "access_count": 0,
            "source_type": "agent",
            "created": datetime.now(timezone.utc).isoformat(),
        }
        old_entry: dict[str, object] = {
            "id": "L-old",
            "summary": "fresh learning about testing",
            "detail": "fresh detail",
            "tags": [],
            "impact": 0.8,
            "q_value": 0.8,
            "q_observations": 5,
            "recurrence": 1,
            "access_count": 0,
            "source_type": "agent",
            "created": (datetime.now(timezone.utc) - timedelta(days=730)).isoformat(),
        }

        ranked = rank_by_utility(
            [old_entry, fresh_entry],
            query_tokens=["fresh", "learning", "testing"],
            lambda_weight=0.3,
        )
        # fresh_entry should appear before old_entry
        ids = [str(e["id"]) for e in ranked]
        assert ids.index("L-fresh") < ids.index("L-old")

    def test_decay_does_not_affect_entries_without_created(self) -> None:
        """Entries without 'created' field fall back to raw impact — no crash."""
        from trw_mcp.scoring import rank_by_utility

        entry_no_created: dict[str, object] = {
            "id": "L-nc",
            "summary": "no created field",
            "detail": "detail",
            "tags": [],
            "impact": 0.8,
            "q_value": 0.8,
            "q_observations": 5,
            "recurrence": 1,
        }
        # Should not raise
        result = rank_by_utility([entry_no_created], query_tokens=[], lambda_weight=0.3)
        assert len(result) == 1
        assert result[0]["id"] == "L-nc"

    def test_decay_with_invalid_created_date_no_crash(self) -> None:
        """Malformed 'created' value falls back to raw impact gracefully."""
        from trw_mcp.scoring import rank_by_utility

        entry_bad_date: dict[str, object] = {
            "id": "L-bad",
            "summary": "bad date",
            "detail": "detail",
            "tags": [],
            "impact": 0.8,
            "q_value": 0.8,
            "q_observations": 5,
            "recurrence": 1,
            "created": "not-a-date",
        }
        result = rank_by_utility([entry_bad_date], query_tokens=[], lambda_weight=0.3)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Config defaults (PRD-CORE-034-FR02)
# ---------------------------------------------------------------------------


class TestOutcomeWindowDefault:
    """Verify the outcome correlation window default is 480 minutes (FR02)."""

    def test_outcome_window_default_is_480(self) -> None:
        from trw_mcp.models.config import TRWConfig
        cfg = TRWConfig()
        assert cfg.learning_outcome_correlation_window_minutes == 480


# ---------------------------------------------------------------------------
# apply_impact_decay (PRD-CORE-034-FR03)
# ---------------------------------------------------------------------------


class TestApplyImpactDecay:
    """Batch impact decay for stale learnings (PRD-CORE-034-FR03)."""

    def test_impact_decay_fresh_entry_unchanged(self) -> None:
        """Entry accessed within half_life_days: impact unchanged."""
        entries: list[dict[str, object]] = [{
            "id": "L-fresh",
            "impact": 0.8,
            "last_accessed_at": _utc(30).isoformat(),  # 30 days ago
        }]
        result = apply_impact_decay(entries, half_life_days=90)
        assert result[0]["impact"] == 0.8  # Within 90-day half life

    def test_impact_decay_stale_entry_decayed(self) -> None:
        """Entry not accessed for > half_life_days: impact decayed."""
        entries: list[dict[str, object]] = [{
            "id": "L-stale",
            "impact": 0.8,
            "last_accessed_at": _utc(180).isoformat(),  # 180 days ago, half_life=90
        }]
        result = apply_impact_decay(entries, half_life_days=90)
        decayed_impact = float(str(result[0]["impact"]))
        # Expected: 0.8 * exp(-0.693 * 90 / 90) = 0.8 * 0.5 = 0.4
        expected = 0.8 * math.exp(-0.693 * (180 - 90) / 90)
        assert decayed_impact == pytest.approx(expected, abs=0.02)
        assert decayed_impact < 0.8

    def test_impact_decay_clamp_floor(self) -> None:
        """Extremely stale entry: impact floored at 0.1."""
        entries: list[dict[str, object]] = [{
            "id": "L-ancient",
            "impact": 0.3,
            "created": _utc(3650).isoformat(),  # 10 years ago
        }]
        result = apply_impact_decay(entries, half_life_days=90)
        decayed_impact = float(str(result[0]["impact"]))
        assert decayed_impact >= 0.1  # Floor enforced

    def test_impact_decay_uses_created_fallback(self) -> None:
        """When last_accessed_at is absent, falls back to created date."""
        entries: list[dict[str, object]] = [{
            "id": "L-no-access",
            "impact": 0.8,
            "created": _utc(180).isoformat(),
        }]
        result = apply_impact_decay(entries, half_life_days=90)
        decayed_impact = float(str(result[0]["impact"]))
        assert decayed_impact < 0.8

    def test_impact_decay_no_date_field_unchanged(self) -> None:
        """Entry with no date fields: impact unchanged."""
        entries: list[dict[str, object]] = [{
            "id": "L-nodate",
            "impact": 0.8,
        }]
        result = apply_impact_decay(entries, half_life_days=90)
        assert result[0]["impact"] == 0.8

    def test_impact_decay_returns_same_list(self) -> None:
        """Returns the same list object (in-place modification)."""
        entries: list[dict[str, object]] = [{
            "id": "L-x",
            "impact": 0.8,
            "created": _utc(200).isoformat(),
        }]
        result = apply_impact_decay(entries, half_life_days=90)
        assert result is entries

    def test_impact_decay_config_default_half_life(self) -> None:
        """When half_life_days=None, uses config default (90)."""
        from trw_mcp.models.config import TRWConfig
        cfg = TRWConfig()
        assert cfg.impact_decay_half_life_days == 90
        # Entry exactly at boundary: no decay
        entries: list[dict[str, object]] = [{
            "id": "L-boundary",
            "impact": 0.8,
            "last_accessed_at": _utc(90).isoformat(),
        }]
        result = apply_impact_decay(entries)  # uses default
        assert float(str(result[0]["impact"])) == 0.8


# ---------------------------------------------------------------------------
# Config field existence (PRD-CORE-034)
# ---------------------------------------------------------------------------


class TestConfigFields:
    """Verify new config fields exist with correct defaults."""

    def test_impact_high_threshold_pct_default(self) -> None:
        from trw_mcp.models.config import TRWConfig
        cfg = TRWConfig()
        assert cfg.impact_high_threshold_pct == 20.0

    def test_impact_decay_half_life_days_default(self) -> None:
        from trw_mcp.models.config import TRWConfig
        cfg = TRWConfig()
        assert cfg.impact_decay_half_life_days == 90
