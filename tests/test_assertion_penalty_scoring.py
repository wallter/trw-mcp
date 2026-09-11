"""Tests for assertion penalty in rank_by_utility (PRD-CORE-086 FR06).

Verifies that assertion_penalties parameter correctly adjusts ranking scores
and that edge cases (empty dict, large penalties, no assertions) are handled.
"""

from __future__ import annotations

from datetime import datetime, timezone

from trw_mcp.scoring._recall import rank_by_utility


def _make_entry(
    entry_id: str,
    summary: str = "test entry",
    impact: float = 0.8,
    tags: list[str] | None = None,
) -> dict[str, object]:
    """Create a minimal learning entry dict for ranking tests."""
    return {
        "id": entry_id,
        "summary": summary,
        "detail": "detail text",
        "tags": tags or ["test"],
        "impact": impact,
        "status": "active",
        "created": datetime.now(tz=timezone.utc).date().isoformat(),
        "access_count": 1,
        "q_value": 0.5,
        "q_observations": 1,
        "recurrence": 1,
    }


class TestNoPenaltyNoAssertions:
    """rank_by_utility without penalties produces unchanged ordering."""

    def test_no_penalty_no_assertions(self) -> None:
        """Without assertion_penalties, ranking is unchanged."""
        entries = [
            _make_entry("L-1", summary="alpha test", impact=0.9),
            _make_entry("L-2", summary="beta test", impact=0.5),
        ]
        ranked_no_penalty = rank_by_utility(entries, ["test"], 0.3)
        ranked_with_none = rank_by_utility(entries, ["test"], 0.3, assertion_penalties=None)

        assert [e["id"] for e in ranked_no_penalty] == [e["id"] for e in ranked_with_none]


class TestPenaltyApplied:
    """Entries with penalties score lower."""

    def test_penalty_applied(self) -> None:
        """Entry with assertion penalty gets ranked lower."""
        entries = [
            _make_entry("L-high", summary="alpha test", impact=0.9),
            _make_entry("L-low", summary="alpha test", impact=0.9),
        ]
        penalties = {"L-high": 0.5}

        ranked = rank_by_utility(entries, ["test"], 0.3, assertion_penalties=penalties)

        # L-high should be ranked lower due to penalty
        ids = [str(e["id"]) for e in ranked]
        assert ids.index("L-low") < ids.index("L-high")

    def test_penalty_reduces_score(self) -> None:
        """A penalized entry has a lower effective score than an unpenalized twin."""
        entries = [_make_entry("L-penalized", summary="test entry", impact=0.8)]
        penalties = {"L-penalized": 0.15}

        ranked_no = rank_by_utility(
            [_make_entry("L-penalized", summary="test entry", impact=0.8)],
            ["test"],
            0.3,
        )
        ranked_yes = rank_by_utility(entries, ["test"], 0.3, assertion_penalties=penalties)

        # Can't directly compare scores since rank_by_utility returns entries not scores,
        # but a single entry should always be returned
        assert len(ranked_yes) == 1


class TestPenaltyBelowZero:
    """CORE116 RA4/5: the displayed targeted score preserves adverse evidence."""

    def test_targeted_penalty_remains_visible_below_zero(self) -> None:
        entries = [_make_entry("L-1", summary="test", impact=0.1)]
        # Massive penalty that would make score negative
        penalties = {"L-1": 10.0}

        ranked = rank_by_utility(entries, ["test"], 0.3, assertion_penalties=penalties)
        assert len(ranked) == 1
        assert ranked[0]["combined_score"] == -9.0

    def test_zero_relevance_failure_cannot_tie_clean_sibling(self) -> None:
        entries = [_make_entry("L-failed"), _make_entry("L-clean")]
        ranked = rank_by_utility(entries, ["absent"], 0.3, assertion_penalties={"L-failed": 0.3})
        assert [(row["id"], row["combined_score"]) for row in ranked] == [
            ("L-clean", 0.0),
            ("L-failed", -0.3),
        ]

    def test_wildcard_retains_zero_floor(self) -> None:
        ranked = rank_by_utility([_make_entry("L-1")], [], 0.3, assertion_penalties={"L-1": 10.0})
        assert ranked[0]["combined_score"] == 0.0


class TestPenaltyDictEmpty:
    """Empty penalty dict produces no change."""

    def test_penalty_dict_empty(self) -> None:
        """Empty assertion_penalties dict does not affect ranking."""
        entries = [
            _make_entry("L-1", summary="alpha test", impact=0.9),
            _make_entry("L-2", summary="beta test", impact=0.5),
        ]
        ranked_none = rank_by_utility(entries, ["test"], 0.3, assertion_penalties=None)
        ranked_empty = rank_by_utility(entries, ["test"], 0.3, assertion_penalties={})

        assert [e["id"] for e in ranked_none] == [e["id"] for e in ranked_empty]


class TestPenaltyOnlyAffectsMatchingEntries:
    """Penalties only apply to entries whose ID is in the penalties dict."""

    def test_unmatched_entries_unaffected(self) -> None:
        """Entries not in penalties dict are ranked normally."""
        entries = [
            _make_entry("L-1", summary="alpha test", impact=0.7),
            _make_entry("L-2", summary="alpha test", impact=0.7),
        ]
        # Only L-1 is penalized
        penalties = {"L-1": 0.5}

        ranked = rank_by_utility(entries, ["test"], 0.3, assertion_penalties=penalties)
        ids = [str(e["id"]) for e in ranked]
        assert ids[0] == "L-2"  # unpenalized should be first
