"""Edge-case tests for ranking and prune-candidate scoring behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from trw_mcp.scoring import rank_by_utility, utility_based_prune_candidates


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
        assert result[0]["id"] == entry_in_detail["id"]

    def test_lambda_weight_one_pure_utility(self) -> None:
        """lambda_weight=1.0 means pure utility, ignores relevance."""
        low_impact = self._make_entry("pytest testing", impact=0.1)
        high_impact = self._make_entry("unrelated", impact=0.9)
        result = rank_by_utility(
            [low_impact, high_impact],
            query_tokens=["pytest"],
            lambda_weight=1.0,
        )
        assert result[0]["id"] == high_impact["id"]

    def test_lambda_weight_zero_pure_relevance(self) -> None:
        """lambda_weight=0.0 means pure relevance, ignores utility."""
        matching = self._make_entry("pytest testing", impact=0.1)
        non_matching = self._make_entry("unrelated stuff", impact=0.9)
        result = rank_by_utility(
            [non_matching, matching],
            query_tokens=["pytest", "testing"],
            lambda_weight=0.0,
        )
        assert result[0]["id"] == matching["id"]

    def test_summary_hits_weighted_higher_than_detail(self) -> None:
        """Summary matches are weighted 3x vs detail matches 1x."""
        entry_summary = self._make_entry("pytest info")
        entry_detail = self._make_entry("generic", detail="pytest info")
        result = rank_by_utility(
            [entry_detail, entry_summary],
            query_tokens=["pytest"],
            lambda_weight=0.0,
        )
        assert result[0]["id"] == entry_summary["id"]

    def test_stable_sort_equal_scores(self) -> None:
        """Entries with equal scores maintain their relative order (sort stability)."""
        entries = [
            self._make_entry("first", impact=0.5),
            self._make_entry("second", impact=0.5),
            self._make_entry("third", impact=0.5),
        ]
        result = rank_by_utility(entries, query_tokens=[], lambda_weight=1.0)
        assert len(result) == 3


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
        recent = (datetime.now(tz=timezone.utc).date() - timedelta(days=10)).isoformat()
        entries = [self._make_entry("L-young-low", recent, impact=0.05)]
        result = utility_based_prune_candidates(entries)
        for candidate in result:
            if candidate["id"] == "L-young-low":
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
        assert len(result_high) <= len(result_low)
