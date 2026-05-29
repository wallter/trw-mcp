"""Time-decay and recall-ranking impact calibration tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests._test_impact_calibration_support import _utc
from trw_mcp.scoring import apply_time_decay


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
        assert (0.9 - decay_high) > (0.3 - decay_low)

    def test_monotonic_decay_with_age(self) -> None:
        """Older entries have lower effective impact (monotonically)."""
        scores = [apply_time_decay(0.8, _utc(d)) for d in [0, 90, 180, 365, 730]]
        for i in range(1, len(scores)):
            assert scores[i] <= scores[i - 1]

    def test_naive_datetime_treated_as_utc(self) -> None:
        """Naive datetimes are treated as UTC (no error)."""
        naive_dt = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc).replace(tzinfo=None)
        result = apply_time_decay(0.8, naive_dt)
        assert 0.0 <= result <= 1.0

    def test_aware_datetime_in_other_tz(self) -> None:
        """Timezone-aware datetimes in non-UTC zones are handled correctly."""
        est = timezone(timedelta(hours=-5))
        past = datetime(2025, 2, 22, 8, 0, 0, tzinfo=est)
        result = apply_time_decay(0.8, past)
        assert 0.0 <= result <= 0.8


class TestRecallRankingDecayIntegration:
    """Verify older learnings rank lower in recall due to time decay."""

    def test_new_entry_ranks_higher_than_old_entry(self) -> None:
        """A recently-accessed entry ranks higher than a stale one (all else equal).

        PRD-QUAL-032: Ranking depends on days_since_last_access (via
        compute_utility_score retention), not on linear time decay from
        created date (which was the double-decay bug).
        """
        from trw_mcp.scoring import rank_by_utility

        now = datetime.now(timezone.utc)
        fresh_entry: dict[str, object] = {
            "id": "L-fresh",
            "summary": "fresh learning about testing",
            "detail": "fresh detail",
            "tags": [],
            "impact": 0.8,
            "q_value": 0.8,
            "q_observations": 5,
            "recurrence": 1,
            "access_count": 3,
            "source_type": "agent",
            "created": now.isoformat(),
            "last_accessed_at": now.strftime("%Y-%m-%d"),
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
            "created": (now - timedelta(days=730)).isoformat(),
            "last_accessed_at": (now - timedelta(days=60)).strftime("%Y-%m-%d"),
        }

        ranked = rank_by_utility(
            [old_entry, fresh_entry],
            query_tokens=["fresh", "learning", "testing"],
            lambda_weight=0.3,
        )
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
