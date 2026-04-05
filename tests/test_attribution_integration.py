"""Integration tests for the attribution pipeline (PRD-CORE-108).

Exercises real component interactions across propensity logging,
IPS computation, selective credit splitting, phase eligibility,
promotion gating, and the full pipeline orchestrator.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Test 1: Propensity log feeds IPS attribution
# ---------------------------------------------------------------------------


class TestPropensityLogFeedsIPS:
    """Verify propensity log entries flow into IPS computation correctly."""

    def test_propensity_log_feeds_ips(self, tmp_path: Path) -> None:
        """Write 15 propensity entries with varying probabilities, read back,
        transform, and verify IPS weighting gives higher weight to lower
        probability entries.
        """
        from trw_mcp.scoring.attribution.ips import compute_ips_attribution
        from trw_mcp.state.propensity_log import log_selection, read_propensity_entries

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        # Write 15 entries with a mix of selection probabilities
        probabilities = [
            0.3, 0.5, 0.8, 0.3, 0.5,
            0.8, 0.3, 0.5, 0.8, 0.3,
            0.5, 0.8, 0.3, 0.5, 0.8,
        ]
        for i, prob in enumerate(probabilities):
            log_selection(
                trw_dir,
                selected="learn-ips-1",
                selection_probability=prob,
                exploration=prob < 0.5,
                context_phase="IMPLEMENT",
                turn=i,
            )

        # Read them back
        entries = read_propensity_entries(trw_dir)
        assert len(entries) == 15

        # Transform entries into the format expected by compute_ips_attribution
        propensity_records: list[dict[str, object]] = [
            {
                "selection_probability": e["selection_probability"],
                "exploration": e["exploration"],
            }
            for e in entries
        ]
        # Construct matching outcomes (all positive = 1.0)
        outcomes: list[dict[str, object]] = [{"value": 1.0} for _ in range(15)]

        result = compute_ips_attribution("learn-ips-1", propensity_records, outcomes)

        # Result should be a valid float and not insufficient_data
        assert isinstance(result.estimate, float)
        assert result.outcome_correlation != "insufficient_data"
        assert result.observations == 15
        assert result.tier == "ips"

        # IPS estimate = mean(outcome / propensity) for each entry
        # With outcome=1.0: entries with prob=0.3 contribute 1/0.3 = 3.33,
        # prob=0.5 contribute 1/0.5 = 2.0, prob=0.8 contribute 1/0.8 = 1.25
        # So the estimate should be > 1.0 (higher than unweighted mean)
        assert result.estimate > 1.0

        # Verify that lower-probability entries produce higher IPS weight:
        # Compute IPS with only low-probability entries vs only high-probability entries
        low_prob_records = [{"selection_probability": 0.3, "exploration": True}] * 15
        high_prob_records = [{"selection_probability": 0.8, "exploration": False}] * 15

        result_low = compute_ips_attribution("l-low", low_prob_records, outcomes)
        result_high = compute_ips_attribution("l-high", high_prob_records, outcomes)

        # Lower propensity -> higher IPS estimate (more inverse weight)
        assert result_low.estimate > result_high.estimate


# ---------------------------------------------------------------------------
# Test 2: Selective credit sums to one
# ---------------------------------------------------------------------------


class TestSelectiveCreditSumsToOne:
    """Verify credit distribution across co-surfaced learnings."""

    def test_selective_credit_sums_to_one(self) -> None:
        """3 co-surfaced learnings with different domain_match scores.
        Shares must sum to 1.0, highest domain_match gets largest share.
        """
        from trw_mcp.scoring.attribution.selective import distribute_credit

        surfaces: list[dict[str, object]] = [
            {"learning_id": "a", "domain_match": 0.9, "temporal_proximity": 0.5},
            {"learning_id": "b", "domain_match": 0.5, "temporal_proximity": 0.5},
            {"learning_id": "c", "domain_match": 0.2, "temporal_proximity": 0.5},
        ]

        shares = distribute_credit(surfaces, outcome_value=1.0)

        # Shares must sum to 1.0
        total = sum(s.share for s in shares)
        assert total == pytest.approx(1.0, abs=1e-9)

        # Map by learning_id for easy lookup
        share_map = {s.learning_id: s.share for s in shares}

        # Highest domain_match gets the largest share
        assert share_map["a"] > share_map["b"]
        assert share_map["b"] > share_map["c"]

        # All shares are positive
        for s in shares:
            assert s.share > 0.0


# ---------------------------------------------------------------------------
# Test 3: Phase eligibility — RESEARCH to VALIDATE
# ---------------------------------------------------------------------------


class TestPhaseEligibilityResearchToValidate:
    """Verify phase distance calculation from RESEARCH to VALIDATE."""

    def test_phase_eligibility_research_to_validate(self) -> None:
        """RESEARCH (index 0) to VALIDATE (index 3) = 3 hops.
        Weight should be 0.7^3 = 0.343.
        """
        from trw_mcp.scoring.attribution.eligibility import (
            _PHASE_ORDER,
            compute_phase_weight,
        )

        # Verify the phase indices
        assert _PHASE_ORDER["RESEARCH"] == 0
        assert _PHASE_ORDER["VALIDATE"] == 3

        # Compute hop count
        hop_count = abs(_PHASE_ORDER["VALIDATE"] - _PHASE_ORDER["RESEARCH"])
        assert hop_count == 3

        # Compute eligibility weight
        weight = compute_phase_weight("RESEARCH", "VALIDATE")
        expected = 0.7 ** 3  # 0.343
        assert weight == pytest.approx(expected, abs=1e-6)
        assert weight == pytest.approx(0.343, abs=1e-3)


# ---------------------------------------------------------------------------
# Test 4: Promotion gate — all 5 criteria pass
# ---------------------------------------------------------------------------


class TestPromotionGateAllFiveCriteria:
    """Verify a learning passing all 5 promotion criteria."""

    def test_promotion_gate_all_five_criteria(self) -> None:
        """Learning with non-empty detail, anchor_validity >= 0.67,
        sessions_surfaced >= 3, outcome_correlation = 'positive',
        and no graph conflicts passes the gate.
        """
        from trw_mcp.scoring.attribution.promotion import check_promotion_gate

        learning: dict[str, object] = {
            "id": "learn-pass-all",
            "detail": "A well-documented learning with sufficient evidence.",
            "anchor_validity": 0.75,
            "sessions_surfaced": 3,
            "outcome_correlation": "positive",
        }

        result = check_promotion_gate(learning, graph_conflicts=None)

        assert result.passed is True
        assert result.failures == []
        assert result.force_promoted is False


# ---------------------------------------------------------------------------
# Test 5: Promotion gate — each failure independently
# ---------------------------------------------------------------------------


class TestPromotionGateEachFailure:
    """Test each of the 5 promotion criteria failing independently."""

    def _make_passing_learning(self) -> dict[str, object]:
        """Create a learning that passes all 5 criteria."""
        return {
            "id": "learn-base",
            "detail": "Sufficient detail content for provenance.",
            "anchor_validity": 0.8,
            "sessions_surfaced": 5,
            "outcome_correlation": "positive",
        }

    def test_empty_detail_fails_provenance(self) -> None:
        """Empty detail -> fails with 'Provenance' in failures."""
        from trw_mcp.scoring.attribution.promotion import check_promotion_gate

        learning = self._make_passing_learning()
        learning["detail"] = ""

        result = check_promotion_gate(learning)
        assert result.passed is False
        assert any("Provenance" in f for f in result.failures)

    def test_low_anchor_validity_fails(self) -> None:
        """anchor_validity = 0.5 -> fails with 'Anchor' in failures."""
        from trw_mcp.scoring.attribution.promotion import check_promotion_gate

        learning = self._make_passing_learning()
        learning["anchor_validity"] = 0.5

        result = check_promotion_gate(learning)
        assert result.passed is False
        assert any("Anchor" in f for f in result.failures)

    def test_insufficient_sessions_fails(self) -> None:
        """sessions_surfaced = 1 -> fails with word related to sessions."""
        from trw_mcp.scoring.attribution.promotion import check_promotion_gate

        learning = self._make_passing_learning()
        learning["sessions_surfaced"] = 1

        result = check_promotion_gate(learning)
        assert result.passed is False
        # The failure message says "Sessions surfaced"
        assert any("Sessions" in f for f in result.failures)

    def test_neutral_outcome_fails(self) -> None:
        """outcome_correlation = 'neutral' -> fails with 'Outcome' in failures."""
        from trw_mcp.scoring.attribution.promotion import check_promotion_gate

        learning = self._make_passing_learning()
        learning["outcome_correlation"] = "neutral"

        result = check_promotion_gate(learning)
        assert result.passed is False
        assert any("Outcome" in f for f in result.failures)

    def test_graph_conflicts_fails(self) -> None:
        """has_conflicts = True -> fails with 'Conflict' in failures."""
        from trw_mcp.scoring.attribution.promotion import check_promotion_gate

        learning = self._make_passing_learning()

        result = check_promotion_gate(learning, graph_conflicts=["conflict-1"])
        assert result.passed is False
        assert any("Conflict" in f or "conflict" in f for f in result.failures)

    def test_each_failure_is_independent(self) -> None:
        """Each criterion fails independently -- only 1 failure per test case."""
        from trw_mcp.scoring.attribution.promotion import check_promotion_gate

        # Test that each failure produces exactly 1 failure reason
        # when only that criterion is broken
        base = self._make_passing_learning()

        # Empty detail only
        learning = dict(base)
        learning["detail"] = ""
        result = check_promotion_gate(learning)
        assert len(result.failures) == 1

        # Low anchor validity only
        learning = dict(base)
        learning["anchor_validity"] = 0.3
        result = check_promotion_gate(learning)
        assert len(result.failures) == 1

        # Low sessions only
        learning = dict(base)
        learning["sessions_surfaced"] = 0
        result = check_promotion_gate(learning)
        assert len(result.failures) == 1

        # Neutral outcome only
        learning = dict(base)
        learning["outcome_correlation"] = "neutral"
        result = check_promotion_gate(learning)
        assert len(result.failures) == 1

        # Graph conflicts only
        learning = dict(base)
        result = check_promotion_gate(learning, graph_conflicts=["c1"])
        assert len(result.failures) == 1


# ---------------------------------------------------------------------------
# Test 6: Full pipeline roundtrip
# ---------------------------------------------------------------------------


class TestFullPipelineRoundtrip:
    """Verify the full attribution pipeline from propensity log to results."""

    def test_full_pipeline_roundtrip(self, tmp_path: Path) -> None:
        """Write propensity data, construct surfaces and outcomes,
        run attribution pipeline, verify results with credit shares.
        """
        from trw_mcp.scoring.attribution.pipeline import run_attribution
        from trw_mcp.state.propensity_log import log_selection, read_propensity_entries

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        learning_ids = ["learn-A", "learn-B"]

        # Write propensity entries for each learning (15 per learning for IPS)
        for lid in learning_ids:
            for i in range(15):
                log_selection(
                    trw_dir,
                    selected=lid,
                    selection_probability=0.5,
                    exploration=True,
                    context_phase="IMPLEMENT",
                    turn=i,
                )

        # Read back all entries
        entries = read_propensity_entries(trw_dir, max_entries=500)
        assert len(entries) == 30

        # Transform to propensity records with learning_id
        propensity_records: list[dict[str, object]] = [
            {
                "learning_id": e["selected"],
                "selection_probability": e["selection_probability"],
                "exploration": e["exploration"],
            }
            for e in entries
        ]

        # Construct surfaces (two co-surfaced learnings)
        surfaces: list[dict[str, object]] = [
            {
                "learning_id": "learn-A",
                "domain_match": 0.9,
                "temporal_proximity": 0.7,
                "source_phase": "IMPLEMENT",
                "target_phase": "VALIDATE",
            },
            {
                "learning_id": "learn-B",
                "domain_match": 0.4,
                "temporal_proximity": 0.5,
                "source_phase": "PLAN",
                "target_phase": "VALIDATE",
            },
        ]

        # Construct outcomes
        outcomes: dict[str, object] = {
            "learn-A": {"value": 0.8},
            "learn-B": {"value": 0.8},
        }

        # Run the full pipeline
        results = run_attribution(
            surfaces=surfaces,
            outcomes=outcomes,
            propensity_records=propensity_records,
        )

        # Verify results
        assert len(results) == 2

        # Both learnings should have attribution updates
        result_ids = {str(r["learning_id"]) for r in results}
        assert result_ids == {"learn-A", "learn-B"}

        # Each result should have required fields
        for r in results:
            assert "outcome_correlation" in r
            assert "credit_share" in r
            assert "sessions_surfaced_delta" in r
            assert r["sessions_surfaced_delta"] == 1

        # Credit shares should sum to 1.0
        total_credit = sum(float(str(r["credit_share"])) for r in results)
        assert total_credit == pytest.approx(1.0, abs=1e-6)

        # learn-A has higher domain_match, should get more credit
        credit_map = {
            str(r["learning_id"]): float(str(r["credit_share"])) for r in results
        }
        assert credit_map["learn-A"] > credit_map["learn-B"]

        # outcome_correlation should not be insufficient_data (we have 15 records each)
        for r in results:
            assert r["outcome_correlation"] != "insufficient_data"
