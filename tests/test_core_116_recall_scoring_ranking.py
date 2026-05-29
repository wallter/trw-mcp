"""Ranking edge-case coverage for CORE-116 recall scoring."""

from __future__ import annotations

from tests._core_116_recall_scoring_support import _make_entry, _score_of


class TestOutcomeBoostInRanking:
    """Tests for string-typed outcome_correlation in rank_by_utility."""

    def test_string_strong_positive_boosts(self) -> None:
        """Entry with outcome_correlation='strong_positive' scores higher."""
        from trw_mcp.scoring._recall import RecallContext, rank_by_utility

        entry_pos = _make_entry(id="L-pos", outcome_correlation="strong_positive")
        entry_neu = _make_entry(id="L-neu", outcome_correlation="neutral")
        ctx = RecallContext(current_phase="IMPLEMENT")

        result_pos = rank_by_utility([entry_pos], query_tokens=["payments"], lambda_weight=0.3, context=ctx)
        result_neu = rank_by_utility([entry_neu], query_tokens=["payments"], lambda_weight=0.3, context=ctx)

        assert _score_of(result_pos) > _score_of(result_neu)

    def test_string_negative_penalizes(self) -> None:
        """Entry with outcome_correlation='negative' scores lower."""
        from trw_mcp.scoring._recall import RecallContext, rank_by_utility

        entry_neg = _make_entry(id="L-neg", outcome_correlation="negative")
        entry_neu = _make_entry(id="L-neu", outcome_correlation="neutral")
        ctx = RecallContext(current_phase="IMPLEMENT")

        result_neg = rank_by_utility([entry_neg], query_tokens=["payments"], lambda_weight=0.3, context=ctx)
        result_neu = rank_by_utility([entry_neu], query_tokens=["payments"], lambda_weight=0.3, context=ctx)

        assert _score_of(result_neg) < _score_of(result_neu)


class TestRankByUtilityEdgeCases:
    """Edge cases for the ranking function."""

    def test_empty_matches_returns_empty(self) -> None:
        """rank_by_utility([]) returns []."""
        from trw_mcp.scoring._recall import rank_by_utility

        result = rank_by_utility([], query_tokens=["payments"], lambda_weight=0.3)
        assert result == []

    def test_combined_score_clamped_to_2(self) -> None:
        """Combined score is clamped to max 2.0."""
        from trw_mcp.scoring._recall import RecallContext, rank_by_utility

        entry = _make_entry(
            id="L-max",
            impact=1.0,
            q_value=1.0,
            domain=["payments"],
            phase_affinity=["VALIDATE"],
            team_origin="checkout",
            outcome_correlation="strong_positive",
            anchor_validity=1.0,
        )
        ctx = RecallContext(
            current_phase="VALIDATE",
            inferred_domains={"payments"},
            team="checkout",
            prd_knowledge_ids={"L-max"},
        )

        result = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3, context=ctx)
        assert _score_of(result) <= 2.0

    def test_results_sorted_descending(self) -> None:
        """Results are sorted by combined_score descending."""
        from trw_mcp.scoring._recall import rank_by_utility

        entries = [
            _make_entry(id="L-low", impact=0.1, q_value=0.1),
            _make_entry(id="L-high", impact=0.9, q_value=0.9),
            _make_entry(id="L-mid", impact=0.5, q_value=0.5),
        ]

        result = rank_by_utility(entries, query_tokens=["payments"], lambda_weight=0.3)

        scores = [_score_of(result, i) for i in range(3)]
        assert scores == sorted(scores, reverse=True)

    def test_original_entries_not_mutated(self) -> None:
        """rank_by_utility returns copies, does not mutate input entries."""
        from trw_mcp.scoring._recall import rank_by_utility

        entry = _make_entry()

        rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3)

        assert "combined_score" not in entry
