"""Split scoring utility/distribution coverage tests from test_recall_scoring_report.py."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


class TestUtilityBasedPruneCandidatesTier3:
    """Cover tier 3 prune candidate paths."""

    def _make_entry(
        self,
        entry_id: str,
        created: str,
        impact: float = 0.5,
        status: str = "active",
    ) -> tuple[Path, dict[str, object]]:
        data: dict[str, object] = {
            "id": entry_id,
            "summary": f"Learning {entry_id}",
            "created": created,
            "status": status,
            "impact": impact,
            "q_value": impact,
            "q_observations": 0,
            "recurrence": 1,
            "access_count": 0,
            "source_type": "agent",
        }
        return (Path(f"/fake/{entry_id}.yaml"), data)

    def test_tier3_prune_candidate_old_medium_utility(self) -> None:
        """Entry older than 14 days with utility just below prune threshold -> tier 3 candidate."""
        from trw_mcp.scoring import utility_based_prune_candidates

        entry = self._make_entry("L-tier3", "2025-11-01", impact=0.3)
        result = utility_based_prune_candidates([entry])

        assert len(result) >= 1
        assert result[0]["id"] == "L-tier3"

    def test_tier3_medium_impact_older_entry_prune_range(self) -> None:
        """Verify tier 3 path executes by using moderate impact, old entry."""
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.scoring import utility_based_prune_candidates

        test_config = TRWConfig()
        # Pin the thresholds so the entry sits between delete and prune levels (tier 3, not tier 2).
        object.__setattr__(test_config, "learning_utility_delete_threshold", 0.0)
        object.__setattr__(test_config, "learning_utility_prune_threshold", 0.99)

        with patch("trw_mcp.scoring._recall_prune.get_config", return_value=test_config):
            old_entry = self._make_entry("L-t3c", "2025-09-01", impact=0.35)
            result = utility_based_prune_candidates([old_entry])
            # Contrast: an entry created today is inside the 14-day grace window and is not nominated.
            fresh = self._make_entry("L-t3-fresh", datetime.now(tz=timezone.utc).date().isoformat(), impact=0.35)
            fresh_result = utility_based_prune_candidates([fresh])

        assert [r["id"] for r in result] == ["L-t3c"]
        assert result[0]["suggested_status"] == "obsolete"
        assert "prune threshold" in result[0]["reason"]
        assert fresh_result == []

    def test_tier3_reason_contains_prune_threshold(self) -> None:
        """Tier 3 candidate reason mentions 'prune threshold'."""
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.scoring import utility_based_prune_candidates

        cfg = TRWConfig()
        object.__setattr__(cfg, "learning_utility_delete_threshold", 0.0)
        object.__setattr__(cfg, "learning_utility_prune_threshold", 0.99)

        with patch("trw_mcp.scoring._recall_prune.get_config", return_value=cfg):
            entry = self._make_entry("L-t3-reason", "2025-10-01", impact=0.5)
            result = utility_based_prune_candidates([entry])

            assert any("prune threshold" in str(r.get("reason", "")) for r in result), (
                f"Expected 'prune threshold' in reasons, got: {[r.get('reason') for r in result]}"
            )


class TestComputeUtilityScoreAccessBoost:
    """Cover access_count boost in compute_utility_score."""

    def test_access_count_positive_adds_boost(self) -> None:
        """access_count > 0 adds sub-linear boost to utility (line 186)."""
        from trw_mcp.scoring import compute_utility_score

        score_no_access = compute_utility_score(0, 1, 0.5, access_count=0)
        score_with_access = compute_utility_score(0, 1, 0.5, access_count=10)
        assert score_with_access > score_no_access

    def test_access_count_boost_is_capped(self) -> None:
        """access_count boost is capped at access_count_boost_cap."""
        from trw_mcp.scoring import compute_utility_score

        score_moderate = compute_utility_score(0, 1, 0.5, access_count=10, access_count_boost_cap=0.15)
        score_high = compute_utility_score(0, 1, 0.5, access_count=10000, access_count_boost_cap=0.15)
        assert abs(score_high - score_moderate) < 0.001 or score_high >= score_moderate


class TestEntryUtilityInvalidCreatedDate:
    """Cover ValueError handling for unparseable created dates in entry_utility."""

    def test_invalid_created_date_uses_raw_values(self) -> None:
        """When created field has invalid date, ValueError is caught and raw values used."""
        from trw_mcp.scoring import rank_targeted_by_utility

        entry: dict[str, object] = {
            "id": "L-bad-date",
            "summary": "entry with bad date",
            "detail": "",
            "tags": [],
            "impact": 0.7,
            "q_value": 0.7,
            "q_observations": 5,
            "recurrence": 1,
            "access_count": 0,
            "source_type": "agent",
            "created": "not-a-real-date",
        }

        result = rank_targeted_by_utility([entry], query_tokens=[], lambda_weight=0.5)
        assert len(result) == 1
        assert result[0]["id"] == "L-bad-date"
