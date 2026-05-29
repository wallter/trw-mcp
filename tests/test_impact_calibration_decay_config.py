"""Impact decay and config-default calibration tests."""

from __future__ import annotations

import math

import pytest

from tests._test_impact_calibration_support import _utc
from trw_mcp.scoring import apply_impact_decay


class TestOutcomeWindowDefault:
    """Verify the outcome correlation window default.

    History: PRD-FIX-070-FR05 lowered 480→60; PRD-FIX-088 FR04 then lowered
    60→7 (a 60-min window matched ~2800 receipts on active sessions, with one
    build_check correlating 2823 entries in 91s; 7 minutes covers a typical
    work cycle while keeping correlation set sizes O(100)). The default is
    pinned in ``models/config/_fields_scoring.py``.
    """

    def test_outcome_window_default_is_7(self) -> None:
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig()
        assert cfg.learning_outcome_correlation_window_minutes == 7


class TestApplyImpactDecay:
    """Batch impact decay for stale learnings (PRD-CORE-034-FR03)."""

    def test_impact_decay_fresh_entry_unchanged(self) -> None:
        """Entry accessed within half_life_days: impact unchanged."""
        entries: list[dict[str, object]] = [
            {
                "id": "L-fresh",
                "impact": 0.8,
                "last_accessed_at": _utc(30).isoformat(),
            }
        ]
        apply_impact_decay(entries, half_life_days=90)
        assert entries[0]["impact"] == 0.8

    def test_impact_decay_stale_entry_decayed(self) -> None:
        """Entry not accessed for > half_life_days: impact decayed."""
        entries: list[dict[str, object]] = [
            {
                "id": "L-stale",
                "impact": 0.8,
                "last_accessed_at": _utc(180).isoformat(),
            }
        ]
        apply_impact_decay(entries, half_life_days=90)
        decayed_impact = float(str(entries[0]["impact"]))
        expected = 0.8 * math.exp(-0.693 * (180 - 90) / 90)
        assert decayed_impact == pytest.approx(expected, abs=0.02)
        assert decayed_impact < 0.8

    def test_impact_decay_clamp_floor(self) -> None:
        """Extremely stale entry: impact floored at 0.1."""
        entries: list[dict[str, object]] = [
            {
                "id": "L-ancient",
                "impact": 0.3,
                "created": _utc(3650).isoformat(),
            }
        ]
        apply_impact_decay(entries, half_life_days=90)
        decayed_impact = float(str(entries[0]["impact"]))
        assert decayed_impact >= 0.1

    def test_impact_decay_uses_created_fallback(self) -> None:
        """When last_accessed_at is absent, falls back to created date."""
        entries: list[dict[str, object]] = [
            {
                "id": "L-no-access",
                "impact": 0.8,
                "created": _utc(180).isoformat(),
            }
        ]
        apply_impact_decay(entries, half_life_days=90)
        decayed_impact = float(str(entries[0]["impact"]))
        assert decayed_impact < 0.8

    def test_impact_decay_no_date_field_unchanged(self) -> None:
        """Entry with no date fields: impact unchanged."""
        entries: list[dict[str, object]] = [{"id": "L-nodate", "impact": 0.8}]
        apply_impact_decay(entries, half_life_days=90)
        assert entries[0]["impact"] == 0.8

    def test_impact_decay_modifies_in_place(self) -> None:
        """Verifies in-place modification (returns None)."""
        entries: list[dict[str, object]] = [
            {
                "id": "L-x",
                "impact": 0.8,
                "created": _utc(200).isoformat(),
            }
        ]
        result = apply_impact_decay(entries, half_life_days=90)
        assert result is None
        decayed_impact = float(str(entries[0]["impact"]))
        assert decayed_impact < 0.8

    def test_impact_decay_config_default_half_life(self) -> None:
        """When half_life_days=None, uses config default (90)."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig()
        assert cfg.impact_decay_half_life_days == 90
        entries: list[dict[str, object]] = [
            {
                "id": "L-boundary",
                "impact": 0.8,
                "last_accessed_at": _utc(90).isoformat(),
            }
        ]
        apply_impact_decay(entries)
        assert float(str(entries[0]["impact"])) == 0.8


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
