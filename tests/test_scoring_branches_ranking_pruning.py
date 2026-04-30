"""Branch tests for ranking and prune candidate selection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from trw_mcp.scoring import rank_by_utility, utility_based_prune_candidates


class TestRankByUtility:
    """Tests for rank_by_utility — re-ranking matched learnings."""

    def _make_entry(self, summary: str, impact: float = 0.5) -> dict[str, object]:
        return {
            "id": f"L-{summary[:4]}",
            "summary": summary,
            "detail": "",
            "tags": [],
            "impact": impact,
            "q_value": impact,
            "q_observations": 5,
            "recurrence": 1,
            "access_count": 0,
            "source_type": "agent",
            "created": "2026-02-01",
        }

    def test_empty_list_returns_empty(self) -> None:
        result = rank_by_utility([], query_tokens=["test"], lambda_weight=0.5)
        assert result == []

    def test_single_entry_returned(self) -> None:
        entries = [self._make_entry("testing framework")]
        result = rank_by_utility(entries, query_tokens=["testing"], lambda_weight=0.5)
        assert len(result) == 1

    def test_higher_relevance_ranked_first(self) -> None:
        """Entry matching query tokens ranks higher with pure relevance."""
        entries = [
            self._make_entry("unrelated content"),
            self._make_entry("testing best practices"),
        ]
        result = rank_by_utility(entries, query_tokens=["testing"], lambda_weight=0.0)
        assert "testing" in str(result[0]["summary"]).lower()

    def test_wildcard_query_uses_utility(self) -> None:
        """Empty query tokens → wildcard mode, pure utility ranking."""
        entries = [
            self._make_entry("low impact entry", impact=0.2),
            self._make_entry("high impact entry", impact=0.9),
        ]
        result = rank_by_utility(entries, query_tokens=[], lambda_weight=1.0)
        assert result[0]["summary"] == "high impact entry"

    def test_human_source_boosts_utility(self) -> None:
        """Human-sourced entries get a utility boost over agent entries."""
        agent_entry = self._make_entry("agent learning", impact=0.7)
        human_entry = self._make_entry("human learning", impact=0.7)
        human_entry["source_type"] = "human"

        result = rank_by_utility([agent_entry, human_entry], query_tokens=[], lambda_weight=1.0)
        assert result[0]["summary"] == "human learning"

    def test_tag_hits_boost_relevance(self) -> None:
        """Tags matching query tokens increase relevance score."""
        entry_with_tag = self._make_entry("generic entry", impact=0.5)
        entry_with_tag["tags"] = ["pytest", "testing"]

        entry_no_tag = self._make_entry("also generic", impact=0.5)
        entry_no_tag["tags"] = []

        result = rank_by_utility(
            [entry_no_tag, entry_with_tag],
            query_tokens=["pytest"],
            lambda_weight=0.0,
        )
        assert result[0]["tags"] == ["pytest", "testing"]


class TestUtilityBasedPruneCandidates:
    """Tests for utility_based_prune_candidates."""

    def _make_entry_tuple(
        self,
        entry_id: str,
        created: str,
        status: str = "active",
        impact: float = 0.3,
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

    def test_empty_entries_returns_empty(self) -> None:
        result = utility_based_prune_candidates([])
        assert result == []

    def test_resolved_status_is_candidate(self) -> None:
        """Entries with resolved status are tier-1 cleanup candidates."""
        entries = [self._make_entry_tuple("L-001", "2026-01-01", status="resolved")]
        result = utility_based_prune_candidates(entries)
        assert len(result) == 1
        assert result[0]["suggested_status"] == "resolved"
        assert "cleanup candidate" in result[0]["reason"]

    def test_obsolete_status_is_candidate(self) -> None:
        """Entries with obsolete status are tier-1 cleanup candidates."""
        entries = [self._make_entry_tuple("L-002", "2026-01-01", status="obsolete")]
        result = utility_based_prune_candidates(entries)
        assert len(result) == 1

    def test_invalid_created_date_skipped(self) -> None:
        """Entries with invalid created date are skipped."""
        entry = self._make_entry_tuple("L-003", "not-a-date")
        result = utility_based_prune_candidates([entry])
        assert result == []

    def test_duplicate_ids_deduplicated(self) -> None:
        """Duplicate IDs are processed only once."""
        entry1 = self._make_entry_tuple("L-dup", "2026-01-01", status="resolved")
        entry2 = self._make_entry_tuple("L-dup", "2026-01-01", status="resolved")
        result = utility_based_prune_candidates([entry1, entry2])
        assert len(result) == 1

    def test_very_old_low_utility_is_tier3_candidate(self) -> None:
        """Very old entry with low impact and utility qualifies as tier-3 candidate."""
        entries = [self._make_entry_tuple("L-old", "2025-08-01", impact=0.1)]
        result = utility_based_prune_candidates(entries)
        assert len(result) == 1
        assert result[0]["id"] == "L-old"
        assert result[0]["suggested_status"] == "obsolete"

    def test_recent_high_utility_not_candidate(self) -> None:
        """Recent entry with high impact is not a prune candidate."""
        recent_date = (datetime.now(timezone.utc).date() - timedelta(days=3)).isoformat()
        entries = [self._make_entry_tuple("L-new", recent_date, impact=0.95)]
        result = utility_based_prune_candidates(entries)
        assert result == []
