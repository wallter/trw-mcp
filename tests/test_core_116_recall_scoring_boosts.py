"""Boost-factor coverage for CORE-116 recall scoring."""

from __future__ import annotations

from tests._core_116_recall_scoring_support import _make_entry, _score_of


class TestOutcomeBoostFactor:
    """Tests for _outcome_boost_factor() string and float mapping."""

    def test_outcome_boost_factor_strong_positive(self) -> None:
        from trw_mcp.scoring._recall import _outcome_boost_factor

        assert _outcome_boost_factor("strong_positive") == 1.5

    def test_outcome_boost_factor_positive(self) -> None:
        from trw_mcp.scoring._recall import _outcome_boost_factor

        assert _outcome_boost_factor("positive") == 1.2

    def test_outcome_boost_factor_neutral(self) -> None:
        from trw_mcp.scoring._recall import _outcome_boost_factor

        assert _outcome_boost_factor("neutral") == 1.0

    def test_outcome_boost_factor_negative(self) -> None:
        from trw_mcp.scoring._recall import _outcome_boost_factor

        assert _outcome_boost_factor("negative") == 0.5

    def test_outcome_boost_factor_float_above_075(self) -> None:
        """Float >= 0.75 maps to 1.5 (strong_positive tier)."""
        from trw_mcp.scoring._recall import _outcome_boost_factor

        assert _outcome_boost_factor(0.8) == 1.5

    def test_outcome_boost_factor_float_above_05(self) -> None:
        """Float >= 0.5 but < 0.75 maps to 1.2 (positive tier)."""
        from trw_mcp.scoring._recall import _outcome_boost_factor

        assert _outcome_boost_factor(0.6) == 1.2

    def test_outcome_boost_factor_float_negative(self) -> None:
        """Float <= -0.5 maps to 0.5 (negative tier)."""
        from trw_mcp.scoring._recall import _outcome_boost_factor

        assert _outcome_boost_factor(-0.7) == 0.5

    def test_outcome_boost_factor_float_neutral_boundary(self) -> None:
        """Float between -0.5 and 0.5 maps to 1.0 (neutral tier)."""
        from trw_mcp.scoring._recall import _outcome_boost_factor

        assert _outcome_boost_factor(0.3) == 1.0

    def test_outcome_boost_unknown_string(self) -> None:
        """Unknown string value defaults to 1.0 (neutral)."""
        from trw_mcp.scoring._recall import _outcome_boost_factor

        assert _outcome_boost_factor("unknown_value") == 1.0

    def test_outcome_boost_factor_float_exact_075(self) -> None:
        """Float exactly 0.75 maps to 1.5 (boundary inclusive)."""
        from trw_mcp.scoring._recall import _outcome_boost_factor

        assert _outcome_boost_factor(0.75) == 1.5

    def test_outcome_boost_factor_float_exact_05(self) -> None:
        """Float exactly 0.5 maps to 1.2 (boundary inclusive)."""
        from trw_mcp.scoring._recall import _outcome_boost_factor

        assert _outcome_boost_factor(0.5) == 1.2

    def test_outcome_boost_factor_float_exact_neg_05(self) -> None:
        """Float exactly -0.5 maps to 0.5 (boundary inclusive)."""
        from trw_mcp.scoring._recall import _outcome_boost_factor

        assert _outcome_boost_factor(-0.5) == 0.5

    def test_outcome_boost_factor_float_clamped_above(self) -> None:
        """Float > 1.0 is clamped to 1.0, then mapped to 1.5."""
        from trw_mcp.scoring._recall import _outcome_boost_factor

        assert _outcome_boost_factor(2.0) == 1.5

    def test_outcome_boost_factor_float_clamped_below(self) -> None:
        """Float < -1.0 is clamped to -1.0, then mapped to 0.5."""
        from trw_mcp.scoring._recall import _outcome_boost_factor

        assert _outcome_boost_factor(-2.0) == 0.5


class TestDomainBoost:
    """Domain match boost dimension (1.4x)."""

    def test_domain_boost_applied(self) -> None:
        """Entry with matching domain scores higher than entry without."""
        from trw_mcp.scoring._recall import RecallContext, rank_by_utility

        entry_match = _make_entry(id="L-match", domain=["payments"])
        entry_no = _make_entry(id="L-no", domain=[])

        ctx = RecallContext(inferred_domains={"payments"})
        ranked = rank_by_utility(
            [entry_match, entry_no],
            query_tokens=["payments"],
            lambda_weight=0.3,
            context=ctx,
        )

        scores = {str(r["id"]): _score_of([r]) for r in ranked}
        assert scores["L-match"] > scores["L-no"]

    def test_domain_boost_no_match(self) -> None:
        """Entry with non-matching domain gets 1.0x (no boost)."""
        from trw_mcp.scoring._recall import RecallContext, rank_by_utility

        entry = _make_entry(domain=["auth"])
        ctx = RecallContext(inferred_domains={"payments"})

        result_ctx = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3, context=ctx)
        result_no = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3)

        assert abs(_score_of(result_ctx) - _score_of(result_no)) < 1e-6


class TestPhaseBoost:
    """Phase affinity boost dimension (1.3x)."""

    def test_phase_boost_applied(self) -> None:
        """Entry with matching phase affinity scores higher."""
        from trw_mcp.scoring._recall import RecallContext, rank_by_utility

        entry = _make_entry(phase_affinity=["VALIDATE"])
        ctx = RecallContext(current_phase="VALIDATE")

        result_ctx = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3, context=ctx)
        result_no = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3)

        assert _score_of(result_ctx) > _score_of(result_no)

    def test_phase_boost_case_insensitive(self) -> None:
        """Phase matching is case-insensitive: 'validate' matches 'VALIDATE'."""
        from trw_mcp.scoring._recall import RecallContext, rank_by_utility

        entry = _make_entry(phase_affinity=["VALIDATE"])
        ctx_lower = RecallContext(current_phase="validate")

        result = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3, context=ctx_lower)
        result_no = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3)

        assert _score_of(result) > _score_of(result_no)


class TestTeamBoost:
    """Team origin boost dimension (1.2x)."""

    def test_team_boost_applied(self) -> None:
        """Entry with matching team origin scores higher."""
        from trw_mcp.scoring._recall import RecallContext, rank_by_utility

        entry = _make_entry(team_origin="checkout")
        ctx = RecallContext(team="checkout")

        result_ctx = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3, context=ctx)
        result_no = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3)

        assert _score_of(result_ctx) > _score_of(result_no)

    def test_team_boost_empty_string(self) -> None:
        """Empty team string in context produces no boost."""
        from trw_mcp.scoring._recall import RecallContext, rank_by_utility

        entry = _make_entry(team_origin="checkout")
        ctx = RecallContext(team="")

        result_ctx = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3, context=ctx)
        result_no = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3)

        assert abs(_score_of(result_ctx) - _score_of(result_no)) < 1e-6


class TestPrdBoost:
    """PRD knowledge ID boost dimension (1.5x)."""

    def test_prd_boost_applied(self) -> None:
        """Entry whose ID is in prd_knowledge_ids scores higher."""
        from trw_mcp.scoring._recall import RecallContext, rank_by_utility

        entry = _make_entry(id="L-test")
        ctx = RecallContext(prd_knowledge_ids={"L-test"})

        result_ctx = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3, context=ctx)
        result_no = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3)

        assert _score_of(result_ctx) > _score_of(result_no)

    def test_prd_boost_not_in_set(self) -> None:
        """Entry not in prd_knowledge_ids gets no PRD boost."""
        from trw_mcp.scoring._recall import RecallContext, rank_by_utility

        entry = _make_entry(id="L-test")
        ctx = RecallContext(prd_knowledge_ids={"L-other"})

        result_ctx = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3, context=ctx)
        result_no = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3)

        assert abs(_score_of(result_ctx) - _score_of(result_no)) < 1e-6


class TestAnchorValidity:
    """Anchor validity multiplicative factor."""

    def test_anchor_validity_multiplicative_half(self) -> None:
        """anchor_validity=0.5 halves the score (not zero)."""
        from trw_mcp.scoring._recall import RecallContext, rank_by_utility

        entry_full = _make_entry(id="L-full", anchor_validity=1.0)
        entry_half = _make_entry(id="L-half", anchor_validity=0.5)
        ctx = RecallContext(current_phase="IMPLEMENT")

        result_full = rank_by_utility([entry_full], query_tokens=["payments"], lambda_weight=0.3, context=ctx)
        result_half = rank_by_utility([entry_half], query_tokens=["payments"], lambda_weight=0.3, context=ctx)

        score_full = _score_of(result_full)
        score_half = _score_of(result_half)
        assert score_half > 0.0
        assert abs(score_half / max(score_full, 1e-9) - 0.5) < 0.05

    def test_anchor_validity_zero(self) -> None:
        """anchor_validity=0.0 results in combined_score 0.0."""
        from trw_mcp.scoring._recall import RecallContext, rank_by_utility

        entry = _make_entry(anchor_validity=0.0)
        ctx = RecallContext(current_phase="IMPLEMENT")

        result = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3, context=ctx)
        assert _score_of(result) == 0.0

    def test_anchor_validity_full(self) -> None:
        """anchor_validity=1.0 applies no penalty."""
        from trw_mcp.scoring._recall import RecallContext, rank_by_utility

        entry = _make_entry(anchor_validity=1.0)
        ctx = RecallContext(current_phase="IMPLEMENT")

        result_ctx = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3, context=ctx)
        result_no = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3)

        assert abs(_score_of(result_ctx) - _score_of(result_no)) < 1e-6


class TestCombinedBoosts:
    """Tests for multiple boost dimensions interacting."""

    def test_all_boosts_combined(self) -> None:
        """Entry matching ALL boost conditions scores much higher than entry matching none."""
        from trw_mcp.scoring._recall import RecallContext, rank_by_utility

        entry_all = _make_entry(
            id="L-all",
            domain=["payments"],
            phase_affinity=["VALIDATE"],
            team_origin="checkout",
            outcome_correlation=0.8,
            anchor_validity=1.0,
        )
        entry_none = _make_entry(
            id="L-none",
            domain=[],
            phase_affinity=[],
            team_origin="",
            outcome_correlation=0.0,
            anchor_validity=1.0,
        )

        ctx = RecallContext(
            current_phase="VALIDATE",
            inferred_domains={"payments"},
            team="checkout",
            prd_knowledge_ids={"L-all"},
        )

        result_all = rank_by_utility([entry_all], query_tokens=["payments"], lambda_weight=0.3, context=ctx)
        result_none = rank_by_utility([entry_none], query_tokens=["payments"], lambda_weight=0.3, context=ctx)

        score_all = _score_of(result_all)
        score_none = _score_of(result_none)
        assert score_all > score_none * 2.0

    def test_no_context_all_boosts_neutral(self) -> None:
        """context=None produces same scores as having no context at all."""
        from trw_mcp.scoring._recall import rank_by_utility

        entry = _make_entry(
            domain=["payments"],
            phase_affinity=["VALIDATE"],
            team_origin="checkout",
            outcome_correlation=0.8,
        )

        result_none = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3, context=None)
        result_omit = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3)

        assert abs(_score_of(result_none) - _score_of(result_omit)) < 1e-6
