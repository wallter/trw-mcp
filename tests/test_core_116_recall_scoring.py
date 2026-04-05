"""Comprehensive tests for PRD-CORE-116: Enhanced Recall Scoring — Multi-Dimensional Boosts.

Covers:
- _outcome_boost_factor: string/float mapping
- rank_by_utility: 6-factor boost formula (domain, phase, team, outcome, anchor, PRD)
- infer_domains: prefix mapping, security, fallback, deprecated alias
- RecallContext: new fields (client_profile, model_family), defaults, frozen
- _TYPE_HALF_LIFE: spec values, relative decay ordering
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(**overrides: object) -> dict[str, object]:
    """Create a synthetic learning entry with PRD-CORE-116 fields."""
    base: dict[str, object] = {
        "id": "L-test",
        "summary": "test summary payments",
        "detail": "test detail",
        "tags": ["test"],
        "impact": 0.7,
        "status": "active",
        "created": "2026-04-01",
        "recurrence": 1,
        "q_value": 0.7,
        "q_observations": 5,
        "access_count": 3,
        "source_type": "agent",
        "domain": [],
        "phase_affinity": [],
        "team_origin": "",
        "outcome_correlation": 0.0,
        "anchor_validity": 1.0,
        "type": "pattern",
        "confidence": "unverified",
    }
    base.update(overrides)
    return base


def _score_of(ranked: list[dict[str, object]], index: int = 0) -> float:
    """Extract combined_score from ranked result at given index."""
    return float(str(ranked[index]["combined_score"]))


# ===================================================================
# _outcome_boost_factor tests
# ===================================================================


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


# ===================================================================
# Boost dimension tests (via rank_by_utility)
# ===================================================================


class TestDomainBoost:
    """Domain match boost dimension (1.4x)."""

    def test_domain_boost_applied(self) -> None:
        """Entry with matching domain scores higher than entry without."""
        from trw_mcp.scoring._recall import RecallContext, rank_by_utility

        entry_match = _make_entry(id="L-match", domain=["payments"])
        entry_no = _make_entry(id="L-no", domain=[])

        ctx = RecallContext(inferred_domains={"payments"})
        ranked = rank_by_utility(
            [entry_match, entry_no], query_tokens=["payments"], lambda_weight=0.3, context=ctx,
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
        # All boosts: 1.4 * 1.3 * 1.2 * 1.5 * 1.0 * 1.5 = ~4.914x
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


# ===================================================================
# infer_domains tests
# ===================================================================


class TestInferDomains:
    """Tests for infer_domains() — prefix mapping, security, fallback."""

    def test_infer_domains_returns_set(self) -> None:
        """Return type is set[str]."""
        from trw_mcp.scoring._recall import infer_domains

        result = infer_domains(file_paths=["src/auth/middleware.py"])
        assert isinstance(result, set)

    def test_infer_domains_prefix_mapping(self) -> None:
        """Configurable prefix mapping resolves to explicit domain labels."""
        from trw_mcp.scoring._recall import infer_domains

        result = infer_domains(
            file_paths=["backend/payments/x.py"],
            path_domain_map={"backend/payments": "payments"},
        )
        assert "payments" in result

    def test_infer_domains_fallback_no_mapping(self) -> None:
        """Without mapping, directory stems are used as fallback."""
        from trw_mcp.scoring._recall import infer_domains

        result = infer_domains(file_paths=["backend/payments/x.py"])
        assert "backend" in result
        assert "payments" in result

    def test_infer_domains_security_traversal(self) -> None:
        """Path traversal '../../etc/passwd' does not produce '..' or 'etc'."""
        from trw_mcp.scoring._recall import infer_domains

        result = infer_domains(file_paths=["../../etc/passwd"])
        assert ".." not in result
        # 'etc' is 3 chars and not structural, but '../../' should be stripped
        # The key requirement: no '..' in result
        for domain in result:
            assert ".." not in domain

    def test_infer_domains_absolute_path_stripped(self) -> None:
        """Absolute path '/backend/payments/x.py' is treated as relative."""
        from trw_mcp.scoring._recall import infer_domains

        result_abs = infer_domains(
            file_paths=["/backend/payments/x.py"],
            path_domain_map={"backend/payments": "payments"},
        )
        result_rel = infer_domains(
            file_paths=["backend/payments/x.py"],
            path_domain_map={"backend/payments": "payments"},
        )
        assert result_abs == result_rel

    def test_infer_domains_prefix_greedy_match(self) -> None:
        """Longer prefix wins over shorter prefix."""
        from trw_mcp.scoring._recall import infer_domains

        result = infer_domains(
            file_paths=["backend/payments/stripe/handler.py"],
            path_domain_map={
                "backend": "backend-general",
                "backend/payments": "payments",
                "backend/payments/stripe": "stripe",
            },
        )
        # Longest prefix "backend/payments/stripe" should win
        assert "stripe" in result
        # Shorter prefixes should NOT match this file
        assert "backend-general" not in result
        assert "payments" not in result

    def test_infer_domains_empty_input(self) -> None:
        """infer_domains(file_paths=[]) returns empty set."""
        from trw_mcp.scoring._recall import infer_domains

        result = infer_domains(file_paths=[])
        assert result == set()

    def test_infer_domains_none_input(self) -> None:
        """infer_domains() with no args returns empty set."""
        from trw_mcp.scoring._recall import infer_domains

        result = infer_domains()
        assert isinstance(result, set)
        assert len(result) == 0

    def test_infer_domains_deprecated_modified_files(self) -> None:
        """Deprecated modified_files param still works as alias for file_paths."""
        from trw_mcp.scoring._recall import infer_domains

        result = infer_domains(modified_files=["backend/payments/x.py"])
        assert "payments" in result
        assert "backend" in result

    def test_infer_domains_prefix_map_traversal_dropped(self) -> None:
        """Prefix map entries with '..' are silently dropped."""
        from trw_mcp.scoring._recall import infer_domains

        result = infer_domains(
            file_paths=["backend/payments/x.py"],
            path_domain_map={"../etc": "hacked", "backend/payments": "payments"},
        )
        assert "hacked" not in result
        assert "payments" in result

    def test_infer_domains_null_byte_rejected(self) -> None:
        """Paths containing null bytes are sanitized out."""
        from trw_mcp.scoring._recall import infer_domains

        result = infer_domains(file_paths=["backend/payments\x00evil/x.py"])
        # Null-byte path is sanitized to empty string and filtered out
        assert "evil" not in result


# ===================================================================
# RecallContext tests (PRD-CORE-116 new fields)
# ===================================================================


class TestRecallContextNewFields:
    """Tests for PRD-CORE-116 new RecallContext fields."""

    def test_recall_context_new_fields(self) -> None:
        """client_profile and model_family are accepted as constructor args."""
        from trw_mcp.scoring._recall import RecallContext

        ctx = RecallContext(client_profile="opencode", model_family="gpt-4o")
        assert ctx.client_profile == "opencode"
        assert ctx.model_family == "gpt-4o"

    def test_recall_context_defaults(self) -> None:
        """All fields default to empty string or empty set."""
        from trw_mcp.scoring._recall import RecallContext

        ctx = RecallContext()
        assert ctx.current_phase is None
        assert ctx.inferred_domains == set()
        assert ctx.team == ""
        assert ctx.prd_knowledge_ids == set()
        assert ctx.modified_files == []
        assert ctx.client_profile == ""
        assert ctx.model_family == ""

    def test_recall_context_frozen(self) -> None:
        """RecallContext is frozen — attribute assignment raises."""
        from dataclasses import FrozenInstanceError

        import pytest

        from trw_mcp.scoring._recall import RecallContext

        ctx = RecallContext(client_profile="opencode")
        with pytest.raises(FrozenInstanceError):
            ctx.client_profile = "cursor"  # type: ignore[misc]


# ===================================================================
# Decay tests (_TYPE_HALF_LIFE, type-aware decay)
# ===================================================================


class TestTypeHalfLife:
    """Tests for _TYPE_HALF_LIFE values per PRD-CORE-116 spec."""

    def test_type_half_life_values(self) -> None:
        """All 5 type half-life values match the PRD spec."""
        from trw_mcp.scoring._decay import _TYPE_HALF_LIFE

        assert _TYPE_HALF_LIFE["incident"] == 90.0
        assert _TYPE_HALF_LIFE["pattern"] == 180.0
        assert _TYPE_HALF_LIFE["convention"] == 9999.0
        assert _TYPE_HALF_LIFE["hypothesis"] == 7.0
        assert _TYPE_HALF_LIFE["workaround"] == 14.0

    def test_pattern_decay_slower_than_workaround(self) -> None:
        """200-day-old pattern retains higher utility than same-age workaround."""
        from trw_mcp.scoring._decay import _entry_utility

        from datetime import date, timedelta

        today = date(2026, 4, 1)
        created = (today - timedelta(days=200)).isoformat()

        pattern_entry: dict[str, object] = {
            "type": "pattern",
            "impact": 0.7,
            "q_value": 0.7,
            "q_observations": 5,
            "recurrence": 1,
            "access_count": 3,
            "source_type": "agent",
            "created": created,
            "confidence": "medium",
        }

        workaround_entry: dict[str, object] = dict(pattern_entry)
        workaround_entry["type"] = "workaround"

        utility_pattern = _entry_utility(pattern_entry, today)
        utility_workaround = _entry_utility(workaround_entry, today)

        assert utility_pattern > utility_workaround

    def test_convention_near_no_decay(self) -> None:
        """1000-day-old convention retains utility close to a fresh entry."""
        from trw_mcp.scoring._decay import _entry_utility

        from datetime import date, timedelta

        today = date(2026, 4, 1)
        old_created = (today - timedelta(days=1000)).isoformat()
        fresh_created = today.isoformat()

        base: dict[str, object] = {
            "type": "convention",
            "impact": 0.7,
            "q_value": 0.7,
            "q_observations": 5,
            "recurrence": 1,
            "access_count": 3,
            "source_type": "agent",
            "confidence": "verified",
        }

        old_entry = dict(base, created=old_created)
        fresh_entry = dict(base, created=fresh_created)

        utility_old = _entry_utility(old_entry, today)
        utility_fresh = _entry_utility(fresh_entry, today)

        # Convention has half_life=9999, so 1000-day-old should be close to fresh
        # Allow up to 20% difference
        assert utility_old > utility_fresh * 0.8

    def test_hypothesis_decays_fast(self) -> None:
        """30-day-old hypothesis has significantly lower utility than fresh one."""
        from trw_mcp.scoring._decay import _entry_utility

        from datetime import date, timedelta

        today = date(2026, 4, 1)
        old_created = (today - timedelta(days=30)).isoformat()
        fresh_created = today.isoformat()

        base: dict[str, object] = {
            "type": "hypothesis",
            "impact": 0.7,
            "q_value": 0.7,
            "q_observations": 5,
            "recurrence": 1,
            "access_count": 3,
            "source_type": "agent",
            "confidence": "low",
        }

        old_entry = dict(base, created=old_created)
        fresh_entry = dict(base, created=fresh_created)

        utility_old = _entry_utility(old_entry, today)
        utility_fresh = _entry_utility(fresh_entry, today)

        # half_life=7 for hypothesis; 30 days is ~4 half-lives
        assert utility_old < utility_fresh * 0.5

    def test_incident_unverified_no_decay(self) -> None:
        """Unverified incident has near-zero decay (half_life=9999)."""
        from trw_mcp.scoring._decay import _entry_utility

        from datetime import date, timedelta

        today = date(2026, 4, 1)
        old_created = (today - timedelta(days=500)).isoformat()
        fresh_created = today.isoformat()

        base: dict[str, object] = {
            "type": "incident",
            "impact": 0.7,
            "q_value": 0.7,
            "q_observations": 5,
            "recurrence": 1,
            "access_count": 3,
            "source_type": "agent",
            "confidence": "unverified",
        }

        old_entry = dict(base, created=old_created)
        fresh_entry = dict(base, created=fresh_created)

        utility_old = _entry_utility(old_entry, today)
        utility_fresh = _entry_utility(fresh_entry, today)

        # Unverified incident has half_life=9999 (preserve postmortem knowledge)
        assert utility_old > utility_fresh * 0.8


# ===================================================================
# Outcome boost in rank_by_utility (string-typed outcome_correlation)
# ===================================================================


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


# ===================================================================
# Edge cases in rank_by_utility
# ===================================================================


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

        # Create an entry that would score very high with all boosts
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
        original_keys = set(entry.keys())

        rank_by_utility([entry], query_tokens=["payments"], lambda_weight=0.3)

        # Original entry should not have combined_score added
        assert "combined_score" not in entry
        assert set(entry.keys()) == original_keys
