"""Tests for scoring.attribution — causal outcome attribution pipeline.

PRD-CORE-108: DML + Causal Estimation Pipeline.
All unit tests, no filesystem I/O.
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# FR01: IPS attribution
# ---------------------------------------------------------------------------


class TestIPSAttribution:
    """Tests for compute_ips_attribution (FR01 Tier 1)."""

    def test_ips_sufficient_data(self) -> None:
        """15 observations produce an outcome_correlation value."""
        from trw_mcp.scoring.attribution.ips import compute_ips_attribution

        propensity_records = [
            {"selection_probability": 0.5, "exploration": True}
            for _ in range(15)
        ]
        outcomes = [{"value": 0.8} for _ in range(15)]
        result = compute_ips_attribution("learn-1", propensity_records, outcomes)
        assert result.observations == 15
        assert result.outcome_correlation != "insufficient_data"
        assert result.tier == "ips"

    def test_ips_insufficient_data(self) -> None:
        """5 observations produce insufficient_data."""
        from trw_mcp.scoring.attribution.ips import compute_ips_attribution

        propensity_records = [
            {"selection_probability": 0.5, "exploration": True}
            for _ in range(5)
        ]
        outcomes = [{"value": 0.8} for _ in range(5)]
        result = compute_ips_attribution("learn-1", propensity_records, outcomes)
        assert result.outcome_correlation == "insufficient_data"
        assert result.observations == 5

    def test_ips_propensity_floor(self) -> None:
        """Propensity of 0.01 is clamped to 0.05 floor."""
        from trw_mcp.scoring.attribution.ips import compute_ips_attribution

        # With propensity 0.01, without floor the weight would be 1/0.01 = 100
        # With floor at 0.05, weight is clamped to 1/0.05 = 20
        propensity_records = [
            {"selection_probability": 0.01, "exploration": True}
            for _ in range(12)
        ]
        outcomes = [{"value": 1.0} for _ in range(12)]
        result = compute_ips_attribution("learn-1", propensity_records, outcomes)

        # If propensity floor works, estimate = sum(1.0/0.05) / 12 = 20.0
        # Without floor: sum(1.0/0.01) / 12 = 100.0
        # The estimate should be 20.0, not 100.0
        assert result.estimate == pytest.approx(20.0, rel=0.01)

    def test_ips_maps_to_strong_positive(self) -> None:
        """Estimate >= 0.75 maps to strong_positive."""
        from trw_mcp.scoring.attribution.ips import compute_ips_attribution

        propensity_records = [
            {"selection_probability": 1.0, "exploration": True}
            for _ in range(15)
        ]
        outcomes = [{"value": 0.9} for _ in range(15)]
        result = compute_ips_attribution("learn-1", propensity_records, outcomes)
        assert result.outcome_correlation == "strong_positive"

    def test_ips_maps_to_positive(self) -> None:
        """Estimate in [0.5, 0.75) maps to positive."""
        from trw_mcp.scoring.attribution.ips import compute_ips_attribution

        propensity_records = [
            {"selection_probability": 1.0, "exploration": True}
            for _ in range(15)
        ]
        outcomes = [{"value": 0.6} for _ in range(15)]
        result = compute_ips_attribution("learn-1", propensity_records, outcomes)
        assert result.outcome_correlation == "positive"

    def test_ips_maps_to_negative(self) -> None:
        """Estimate <= -0.5 maps to negative."""
        from trw_mcp.scoring.attribution.ips import compute_ips_attribution

        propensity_records = [
            {"selection_probability": 1.0, "exploration": True}
            for _ in range(15)
        ]
        outcomes = [{"value": -0.8} for _ in range(15)]
        result = compute_ips_attribution("learn-1", propensity_records, outcomes)
        assert result.outcome_correlation == "negative"

    def test_ips_maps_to_neutral(self) -> None:
        """Estimate in (-0.5, 0.5) maps to neutral."""
        from trw_mcp.scoring.attribution.ips import compute_ips_attribution

        propensity_records = [
            {"selection_probability": 1.0, "exploration": True}
            for _ in range(15)
        ]
        outcomes = [{"value": 0.2} for _ in range(15)]
        result = compute_ips_attribution("learn-1", propensity_records, outcomes)
        assert result.outcome_correlation == "neutral"

    def test_ips_client_profile_and_model_family(self) -> None:
        """Client profile and model family are passed through."""
        from trw_mcp.scoring.attribution.ips import compute_ips_attribution

        propensity_records = [
            {"selection_probability": 0.5, "exploration": True}
            for _ in range(15)
        ]
        outcomes = [{"value": 0.8} for _ in range(15)]
        result = compute_ips_attribution(
            "learn-1",
            propensity_records,
            outcomes,
            client_profile="claude-code",
            model_family="opus",
        )
        assert result.client_profile == "claude-code"
        assert result.model_family == "opus"


# ---------------------------------------------------------------------------
# FR02: Selective attribution (ERM credit splitting)
# ---------------------------------------------------------------------------


class TestSelectiveAttribution:
    """Tests for distribute_credit (FR02)."""

    def test_selective_attribution_two_learnings(self) -> None:
        """Domain_match 0.9 vs 0.3 gives roughly 65/35 split."""
        from trw_mcp.scoring.attribution.selective import distribute_credit

        surfaces = [
            {"learning_id": "a", "domain_match": 0.9, "temporal_proximity": 0.5},
            {"learning_id": "b", "domain_match": 0.3, "temporal_proximity": 0.5},
        ]
        shares = distribute_credit(surfaces, outcome_value=1.0)
        assert len(shares) == 2
        share_a = next(s for s in shares if s.learning_id == "a")
        share_b = next(s for s in shares if s.learning_id == "b")
        # Higher domain match should get more credit
        assert share_a.share > share_b.share
        # Rough check — not exact because softmax
        assert share_a.share > 0.55
        assert share_b.share < 0.45

    def test_selective_attribution_sums_to_one(self) -> None:
        """3 learnings — shares sum to 1.0."""
        from trw_mcp.scoring.attribution.selective import distribute_credit

        surfaces = [
            {"learning_id": "a", "domain_match": 0.8, "temporal_proximity": 0.9},
            {"learning_id": "b", "domain_match": 0.5, "temporal_proximity": 0.5},
            {"learning_id": "c", "domain_match": 0.2, "temporal_proximity": 0.1},
        ]
        shares = distribute_credit(surfaces, outcome_value=1.0)
        total = sum(s.share for s in shares)
        assert total == pytest.approx(1.0, abs=1e-9)

    def test_selective_attribution_temperature(self) -> None:
        """Lower temperature produces sharper distribution."""
        from trw_mcp.scoring.attribution.selective import distribute_credit

        surfaces = [
            {"learning_id": "a", "domain_match": 0.9, "temporal_proximity": 0.5},
            {"learning_id": "b", "domain_match": 0.3, "temporal_proximity": 0.5},
        ]
        shares_warm = distribute_credit(surfaces, outcome_value=1.0, temperature=2.0)
        shares_cold = distribute_credit(surfaces, outcome_value=1.0, temperature=0.5)

        top_warm = max(s.share for s in shares_warm)
        top_cold = max(s.share for s in shares_cold)
        # Lower temp = sharper, so top share should be higher
        assert top_cold > top_warm

    def test_selective_attribution_single_surface(self) -> None:
        """Single surface gets 100% credit."""
        from trw_mcp.scoring.attribution.selective import distribute_credit

        surfaces = [
            {"learning_id": "a", "domain_match": 0.5, "temporal_proximity": 0.5},
        ]
        shares = distribute_credit(surfaces, outcome_value=1.0)
        assert len(shares) == 1
        assert shares[0].share == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# FR03: Phase-distance eligibility traces
# ---------------------------------------------------------------------------


class TestPhaseWeight:
    """Tests for compute_phase_weight (FR03)."""

    def test_phase_weight_same_phase(self) -> None:
        """0 hops produces weight 1.0."""
        from trw_mcp.scoring.attribution.eligibility import compute_phase_weight

        assert compute_phase_weight("IMPLEMENT", "IMPLEMENT") == pytest.approx(1.0)

    def test_phase_weight_3_hops(self) -> None:
        """RESEARCH to VALIDATE = 3 hops, weight = 0.7^3 = 0.343."""
        from trw_mcp.scoring.attribution.eligibility import compute_phase_weight

        weight = compute_phase_weight("RESEARCH", "VALIDATE")
        assert weight == pytest.approx(0.7**3, abs=1e-6)

    def test_phase_weight_custom_decay(self) -> None:
        """Custom decay=0.8, 3 hops gives 0.8^3 = 0.512."""
        from trw_mcp.scoring.attribution.eligibility import compute_phase_weight

        weight = compute_phase_weight("RESEARCH", "VALIDATE", decay_factor=0.8)
        assert weight == pytest.approx(0.8**3, abs=1e-6)

    def test_phase_weight_unknown_phase(self) -> None:
        """Unknown phase produces safe default of 1.0."""
        from trw_mcp.scoring.attribution.eligibility import compute_phase_weight

        assert compute_phase_weight("UNKNOWN", "VALIDATE") == pytest.approx(1.0)
        assert compute_phase_weight("RESEARCH", "UNKNOWN") == pytest.approx(1.0)

    def test_phase_weight_reverse_direction(self) -> None:
        """Phase distance is absolute — DELIVER to RESEARCH same as RESEARCH to DELIVER."""
        from trw_mcp.scoring.attribution.eligibility import compute_phase_weight

        fwd = compute_phase_weight("RESEARCH", "DELIVER")
        rev = compute_phase_weight("DELIVER", "RESEARCH")
        assert fwd == pytest.approx(rev)

    def test_phase_weight_adjacent(self) -> None:
        """Adjacent phases: 1 hop = decay_factor."""
        from trw_mcp.scoring.attribution.eligibility import compute_phase_weight

        weight = compute_phase_weight("RESEARCH", "PLAN")
        assert weight == pytest.approx(0.7, abs=1e-6)


# ---------------------------------------------------------------------------
# FR04: Promotion safety gate
# ---------------------------------------------------------------------------


class TestPromotionGate:
    """Tests for check_promotion_gate and force_promote (FR04)."""

    def _make_passing_learning(self) -> dict[str, object]:
        """Create a learning dict that passes all 5 criteria."""
        return {
            "id": "learn-1",
            "detail": "A non-empty detail field with enough content.",
            "anchor_validity": 0.8,
            "sessions_surfaced": 5,
            "outcome_correlation": "positive",
        }

    def test_promotion_gate_all_pass(self) -> None:
        """Learning meeting all criteria passes."""
        from trw_mcp.scoring.attribution.promotion import check_promotion_gate

        learning = self._make_passing_learning()
        result = check_promotion_gate(learning)
        assert result.passed is True
        assert result.failures == []
        assert result.force_promoted is False

    def test_promotion_gate_insufficient_sessions(self) -> None:
        """sessions_surfaced=2 is rejected."""
        from trw_mcp.scoring.attribution.promotion import check_promotion_gate

        learning = self._make_passing_learning()
        learning["sessions_surfaced"] = 2
        result = check_promotion_gate(learning)
        assert result.passed is False
        assert any("sessions" in f.lower() for f in result.failures)

    def test_promotion_gate_neutral_outcome(self) -> None:
        """outcome_correlation='neutral' is rejected."""
        from trw_mcp.scoring.attribution.promotion import check_promotion_gate

        learning = self._make_passing_learning()
        learning["outcome_correlation"] = "neutral"
        result = check_promotion_gate(learning)
        assert result.passed is False
        assert any("outcome" in f.lower() for f in result.failures)

    def test_promotion_gate_unresolved_conflict(self) -> None:
        """Conflicts present causes rejection."""
        from trw_mcp.scoring.attribution.promotion import check_promotion_gate

        learning = self._make_passing_learning()
        result = check_promotion_gate(learning, graph_conflicts=["conflict-1"])
        assert result.passed is False
        assert any("conflict" in f.lower() for f in result.failures)

    def test_promotion_gate_low_anchor_validity(self) -> None:
        """anchor_validity=0.5 is rejected (below 0.67 threshold)."""
        from trw_mcp.scoring.attribution.promotion import check_promotion_gate

        learning = self._make_passing_learning()
        learning["anchor_validity"] = 0.5
        result = check_promotion_gate(learning)
        assert result.passed is False
        assert any("anchor" in f.lower() for f in result.failures)

    def test_promotion_gate_empty_detail(self) -> None:
        """Empty detail field is rejected."""
        from trw_mcp.scoring.attribution.promotion import check_promotion_gate

        learning = self._make_passing_learning()
        learning["detail"] = ""
        result = check_promotion_gate(learning)
        assert result.passed is False
        assert any("provenance" in f.lower() or "detail" in f.lower() for f in result.failures)

    def test_force_promote_logs_failures(self) -> None:
        """force_promote with failing criteria still passes but records failures."""
        from trw_mcp.scoring.attribution.promotion import force_promote

        learning: dict[str, object] = {
            "id": "learn-bad",
            "detail": "",
            "anchor_validity": 0.3,
            "sessions_surfaced": 1,
            "outcome_correlation": "neutral",
        }
        result = force_promote(
            learning,
            reason="Override for testing",
            agent_identity="test-agent",
        )
        assert result.passed is True
        assert result.force_promoted is True
        assert len(result.failures) > 0  # Some criteria failed

    def test_promotion_gate_missing_fields_use_defaults(self) -> None:
        """Missing optional fields use safe defaults (reject)."""
        from trw_mcp.scoring.attribution.promotion import check_promotion_gate

        learning: dict[str, object] = {"id": "learn-bare"}
        result = check_promotion_gate(learning)
        assert result.passed is False


# ---------------------------------------------------------------------------
# FR05: Pipeline orchestrator
# ---------------------------------------------------------------------------


class TestAttributionPipeline:
    """Tests for run_attribution (FR05)."""

    def test_pipeline_runs_attribution(self) -> None:
        """Full pipeline with synthetic data produces attribution updates."""
        from trw_mcp.scoring.attribution.pipeline import run_attribution

        surfaces = [
            {
                "learning_id": "learn-1",
                "domain_match": 0.8,
                "temporal_proximity": 0.5,
                "source_phase": "IMPLEMENT",
                "target_phase": "VALIDATE",
            },
        ]
        outcomes = {
            "learn-1": {"value": 0.8},
        }
        propensity_records = [
            {"learning_id": "learn-1", "selection_probability": 0.5, "exploration": True}
            for _ in range(15)
        ]
        results = run_attribution(surfaces, outcomes, propensity_records)
        assert len(results) == 1
        assert results[0]["learning_id"] == "learn-1"
        assert "outcome_correlation" in results[0]
        assert "credit_share" in results[0]

    def test_pipeline_no_econml_graceful(self) -> None:
        """When econml is not installed, DML is skipped gracefully."""
        from trw_mcp.scoring.attribution.pipeline import run_attribution

        surfaces = [
            {
                "learning_id": "learn-1",
                "domain_match": 0.8,
                "temporal_proximity": 0.5,
                "source_phase": "IMPLEMENT",
                "target_phase": "VALIDATE",
            },
        ]
        # Only 3 propensity records (below 10 threshold) — would trigger DML fallback
        outcomes = {"learn-1": {"value": 0.8}}
        propensity_records = [
            {"learning_id": "learn-1", "selection_probability": 0.5, "exploration": True}
            for _ in range(3)
        ]
        # EconML is not installed in test env, so DML should be skipped
        results = run_attribution(surfaces, outcomes, propensity_records)
        assert len(results) == 1
        # With insufficient IPS data and no EconML, should get insufficient_data
        assert results[0]["outcome_correlation"] == "insufficient_data"

    def test_pipeline_multiple_surfaces_credit_splitting(self) -> None:
        """Multiple co-surfaced learnings get credit splitting via FR02."""
        from trw_mcp.scoring.attribution.pipeline import run_attribution

        surfaces = [
            {
                "learning_id": "learn-1",
                "domain_match": 0.9,
                "temporal_proximity": 0.5,
                "source_phase": "IMPLEMENT",
                "target_phase": "VALIDATE",
            },
            {
                "learning_id": "learn-2",
                "domain_match": 0.3,
                "temporal_proximity": 0.5,
                "source_phase": "PLAN",
                "target_phase": "VALIDATE",
            },
        ]
        outcomes = {
            "learn-1": {"value": 0.8},
            "learn-2": {"value": 0.8},
        }
        propensity_records = [
            {"learning_id": "learn-1", "selection_probability": 0.5, "exploration": True}
            for _ in range(15)
        ] + [
            {"learning_id": "learn-2", "selection_probability": 0.5, "exploration": True}
            for _ in range(15)
        ]
        results = run_attribution(surfaces, outcomes, propensity_records)
        assert len(results) == 2
        shares = [r["credit_share"] for r in results]
        assert sum(shares) == pytest.approx(1.0, abs=1e-6)

    def test_pipeline_empty_surfaces(self) -> None:
        """Empty surfaces list produces empty results."""
        from trw_mcp.scoring.attribution.pipeline import run_attribution

        results = run_attribution([], {}, [])
        assert results == []


# ---------------------------------------------------------------------------
# Model field additions (LearningEntry)
# ---------------------------------------------------------------------------


class TestLearningEntryAttributionFields:
    """Verify new fields on LearningEntry model."""

    def test_outcome_correlation_default(self) -> None:
        """outcome_correlation defaults to empty string."""
        from trw_mcp.models.learning import LearningEntry

        entry = LearningEntry(id="test-1", summary="test", detail="detail")
        assert entry.outcome_correlation == ""

    def test_sessions_surfaced_default(self) -> None:
        """sessions_surfaced defaults to 0."""
        from trw_mcp.models.learning import LearningEntry

        entry = LearningEntry(id="test-1", summary="test", detail="detail")
        assert entry.sessions_surfaced == 0

    def test_avg_rework_delta_default(self) -> None:
        """avg_rework_delta defaults to None."""
        from trw_mcp.models.learning import LearningEntry

        entry = LearningEntry(id="test-1", summary="test", detail="detail")
        assert entry.avg_rework_delta is None

    def test_sessions_surfaced_rejects_negative(self) -> None:
        """sessions_surfaced rejects negative values."""
        from pydantic import ValidationError

        from trw_mcp.models.learning import LearningEntry

        with pytest.raises(ValidationError):
            LearningEntry(
                id="test-1",
                summary="test",
                detail="detail",
                sessions_surfaced=-1,
            )
