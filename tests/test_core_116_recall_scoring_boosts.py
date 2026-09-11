"""CORE-116 RA2: positive preferences break ties; anchor evidence qualifies relevance."""

from __future__ import annotations

from tests._core_116_recall_scoring_support import _make_entry, _score_of


def _preference_of(ranked: list[dict[str, object]], index: int = 0) -> float:
    """Positive context changes only the secondary score, never relevance."""
    assert _score_of(ranked, index) == 1.0
    return float(str(ranked[index]["preference_score"]))


class TestDomainBoost:
    """Domain match boost dimension (1.4x)."""

    def test_domain_boost_applied(self) -> None:
        """Entry with matching domain scores higher than entry without."""
        from trw_mcp.scoring._recall import RecallContext, rank_by_utility

        entry_match = _make_entry(id="L-match", domain=["payments"])
        entry_no = _make_entry(id="L-no", domain=[])

        ctx = RecallContext(inferred_domains={"payments"})
        ranked = rank_by_utility(
            [entry_no, entry_match],
            query_tokens=["payments"],
            lambda_weight=0.3,
            context=ctx,
        )

        scores = {str(r["id"]): _preference_of([r]) for r in ranked}
        assert scores["L-match"] > scores["L-no"]
        assert ranked[0]["id"] == "L-match"

    def test_domain_boost_no_match(self) -> None:
        """Entry with non-matching domain gets 1.0x (no boost)."""
        from trw_mcp.scoring._recall import RecallContext, rank_by_utility

        entry = _make_entry(domain=["auth"])
        ctx = RecallContext(inferred_domains={"payments"})

        result_ctx = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3, context=ctx)
        result_no = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3)

        assert abs(_preference_of(result_ctx) - _preference_of(result_no)) < 1e-6


class TestPhaseBoost:
    """Phase affinity boost dimension (1.3x)."""

    def test_phase_boost_applied(self) -> None:
        """Entry with matching phase affinity scores higher."""
        from trw_mcp.scoring._recall import RecallContext, rank_by_utility

        entry = _make_entry(phase_affinity=["VALIDATE"])
        ctx = RecallContext(current_phase="VALIDATE")

        result_ctx = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3, context=ctx)
        result_no = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3)

        assert _preference_of(result_ctx) > _preference_of(result_no)

    def test_phase_boost_case_insensitive(self) -> None:
        """Phase matching is case-insensitive: 'validate' matches 'VALIDATE'."""
        from trw_mcp.scoring._recall import RecallContext, rank_by_utility

        entry = _make_entry(phase_affinity=["VALIDATE"])
        ctx_lower = RecallContext(current_phase="validate")

        result = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3, context=ctx_lower)
        result_no = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3)

        assert _preference_of(result) > _preference_of(result_no)


class TestTeamBoost:
    """Team origin boost dimension (1.2x)."""

    def test_team_boost_applied(self) -> None:
        """Entry with matching team origin scores higher."""
        from trw_mcp.scoring._recall import RecallContext, rank_by_utility

        entry = _make_entry(team_origin="checkout")
        ctx = RecallContext(team="checkout")

        result_ctx = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3, context=ctx)
        result_no = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3)

        assert _preference_of(result_ctx) > _preference_of(result_no)

    def test_team_boost_empty_string(self) -> None:
        """Empty team string in context produces no boost."""
        from trw_mcp.scoring._recall import RecallContext, rank_by_utility

        entry = _make_entry(team_origin="checkout")
        ctx = RecallContext(team="")

        result_ctx = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3, context=ctx)
        result_no = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3)

        assert abs(_preference_of(result_ctx) - _preference_of(result_no)) < 1e-6


class TestPrdBoost:
    """PRD knowledge ID boost dimension (1.5x)."""

    def test_prd_boost_applied(self) -> None:
        """Entry whose ID is in prd_knowledge_ids scores higher."""
        from trw_mcp.scoring._recall import RecallContext, rank_by_utility

        entry = _make_entry(id="L-test")
        ctx = RecallContext(prd_knowledge_ids={"L-test"})

        result_ctx = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3, context=ctx)
        result_no = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3)

        assert _preference_of(result_ctx) > _preference_of(result_no)

    def test_prd_boost_not_in_set(self) -> None:
        """Entry not in prd_knowledge_ids gets no PRD boost."""
        from trw_mcp.scoring._recall import RecallContext, rank_by_utility

        entry = _make_entry(id="L-test")
        ctx = RecallContext(prd_knowledge_ids={"L-other"})

        result_ctx = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3, context=ctx)
        result_no = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3)

        assert abs(_preference_of(result_ctx) - _preference_of(result_no)) < 1e-6


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
            anchor_validity=1.0,
        )
        entry_none = _make_entry(
            id="L-none",
            domain=[],
            phase_affinity=[],
            team_origin="",
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

        score_all = _preference_of(result_all)
        score_none = _preference_of(result_none)
        assert abs(score_all / score_none - 1.4 * 1.3 * 1.2 * 1.5) < 0.01
        # Reverse the input so a passing tie-break cannot be a stable-sort accident.
        ranked = rank_by_utility([entry_none, entry_all], ["payments"], 0.3, context=ctx)
        assert ranked[0]["id"] == "L-all"
        assert _score_of(ranked, 0) == _score_of(ranked, 1)

    def test_no_context_all_boosts_neutral(self) -> None:
        """context=None produces same scores as having no context at all."""
        from trw_mcp.scoring._recall import rank_by_utility

        entry = _make_entry(
            domain=["payments"],
            phase_affinity=["VALIDATE"],
            team_origin="checkout",
        )

        result_none = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3, context=None)
        result_omit = rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3)

        assert abs(_preference_of(result_none) - _preference_of(result_omit)) < 1e-6
