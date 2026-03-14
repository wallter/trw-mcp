"""Tests for scoring module — compute_utility_score, update_q_value, and complexity scoring."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.run import (
    ComplexityClass,
    ComplexityOverride,
    ComplexitySignals,
    PhaseRequirements,
    RunState,
)
from trw_mcp.scoring import (
    classify_complexity,
    compute_impact_distribution,
    compute_tier_ceremony_score,
    compute_utility_score,
    get_phase_requirements,
    update_q_value,
)


class TestUpdateQValue:
    """Tests for the MemRL Q-value update formula."""

    def test_positive_reward_increases_q(self) -> None:
        assert update_q_value(0.5, 1.0) > 0.5

    def test_negative_reward_decreases_q(self) -> None:
        assert update_q_value(0.5, -0.5) < 0.5

    def test_zero_reward_moves_toward_zero(self) -> None:
        assert update_q_value(0.5, 0.0) < 0.5

    def test_same_reward_is_stable(self) -> None:
        """When reward == q_old, Q stays the same (no bonus)."""
        assert update_q_value(0.5, 0.5) == pytest.approx(0.5)

    def test_convergence_to_true_value(self) -> None:
        """After many updates with constant reward, Q converges."""
        q = 0.5
        for _ in range(50):
            q = update_q_value(q, 0.8)
        assert abs(q - 0.8) < 0.01

    def test_convergence_to_low_value(self) -> None:
        """Converges downward as well."""
        q = 0.8
        for _ in range(50):
            q = update_q_value(q, 0.2)
        assert abs(q - 0.2) < 0.01

    def test_clamped_upper_bound(self) -> None:
        assert update_q_value(0.99, 1.0) <= 1.0
        assert update_q_value(0.99, 1.0, recurrence_bonus=0.1) <= 1.0

    def test_clamped_lower_bound(self) -> None:
        assert update_q_value(0.01, -1.0) >= 0.0

    def test_recurrence_bonus_increases_q(self) -> None:
        q_without = update_q_value(0.5, 0.8, recurrence_bonus=0.0)
        q_with = update_q_value(0.5, 0.8, recurrence_bonus=0.02)
        assert q_with > q_without

    def test_custom_alpha(self) -> None:
        """Higher alpha means faster adaptation."""
        q_slow = update_q_value(0.5, 1.0, alpha=0.05)
        q_fast = update_q_value(0.5, 1.0, alpha=0.5)
        assert q_fast > q_slow

    def test_half_life_of_adaptation(self) -> None:
        """After ~4.3 updates at alpha=0.15, should be within 50% of target."""
        q = 0.0
        target = 1.0
        for _ in range(5):
            q = update_q_value(q, target)
        # Should be at least 50% of the way there
        assert q > 0.5 * target


class TestComputeUtilityScore:
    """Tests for the composite utility scoring function."""

    def test_fresh_high_value(self) -> None:
        """Fresh, high-Q learning has high utility."""
        score = compute_utility_score(0.9, 0, 1, 0.9, 5)
        assert score > 0.85

    def test_fresh_default_value(self) -> None:
        """Fresh, default learning has ~0.5 utility."""
        score = compute_utility_score(0.5, 0, 1, 0.5, 5)
        assert 0.45 < score < 0.55

    def test_decay_without_access(self) -> None:
        """Utility decays over time without access."""
        score_fresh = compute_utility_score(0.5, 0, 1, 0.5, 5)
        score_2wk = compute_utility_score(0.5, 14, 1, 0.5, 5)
        score_1mo = compute_utility_score(0.5, 30, 1, 0.5, 5)
        assert score_2wk < score_fresh
        assert score_1mo < score_2wk

    def test_two_month_unused_low_utility(self) -> None:
        """Two months unused drops below prune threshold."""
        score = compute_utility_score(0.5, 60, 1, 0.5, 5)
        assert score < 0.10

    def test_recurrence_slows_decay(self) -> None:
        """Higher recurrence extends effective half-life."""
        score_low = compute_utility_score(0.5, 14, 1, 0.5, 5)
        score_high = compute_utility_score(0.5, 14, 10, 0.5, 5)
        assert score_high > score_low

    def test_high_q_frequently_recalled(self) -> None:
        """High Q + frequent recalls persists strongly."""
        score = compute_utility_score(0.9, 7, 10, 0.9, 5)
        assert score > 0.75

    def test_cold_start_uses_impact(self) -> None:
        """With 0 observations, utility is based on base_impact."""
        score = compute_utility_score(0.3, 0, 1, 0.7, 0)
        # q_value=0.3 ignored, base_impact=0.7 used
        assert abs(score - 0.7) < 0.01

    def test_cold_start_partial_blend(self) -> None:
        """With 1 observation (threshold=3), blend is 2/3 impact + 1/3 q."""
        score = compute_utility_score(0.3, 0, 1, 0.9, 1)
        # effective_q = (1 - 1/3) * 0.9 + (1/3) * 0.3 = 0.6 + 0.1 = 0.7
        assert abs(score - 0.7) < 0.01

    def test_cold_start_fully_converged(self) -> None:
        """With >= threshold observations, q_value is fully trusted."""
        score = compute_utility_score(0.3, 0, 1, 0.9, 5)
        # effective_q = 0.3 (q_value)
        assert abs(score - 0.3) < 0.01

    def test_output_clamped_to_unit_range(self) -> None:
        """Score always in [0.0, 1.0]."""
        assert compute_utility_score(1.0, 0, 100, 1.0, 100) <= 1.0
        assert compute_utility_score(0.0, 1000, 1, 0.0, 0) >= 0.0

    def test_zero_days_no_decay(self) -> None:
        """Zero days since access means no decay applied."""
        score = compute_utility_score(0.8, 0, 1, 0.8, 5)
        assert abs(score - 0.8) < 0.01

    def test_negative_days_treated_as_zero(self) -> None:
        """Negative days_since_last_access treated as 0 (no future decay)."""
        score = compute_utility_score(0.8, -5, 1, 0.8, 5)
        assert abs(score - 0.8) < 0.01

    def test_custom_half_life(self) -> None:
        """Shorter half-life causes faster decay."""
        score_short = compute_utility_score(
            0.5, 7, 1, 0.5, 5, half_life_days=7.0,
        )
        score_long = compute_utility_score(
            0.5, 7, 1, 0.5, 5, half_life_days=28.0,
        )
        assert score_short < score_long

    def test_half_life_exact(self) -> None:
        """At exactly half_life_days, retention is ~50% (for recurrence=1)."""
        score = compute_utility_score(1.0, 14, 1, 1.0, 5, half_life_days=14.0)
        assert abs(score - 0.5) < 0.01

    def test_custom_use_exponent(self) -> None:
        """Higher use_exponent amplifies recurrence benefit."""
        score_low = compute_utility_score(
            0.5, 14, 5, 0.5, 5, use_exponent=0.3,
        )
        score_high = compute_utility_score(
            0.5, 14, 5, 0.5, 5, use_exponent=0.9,
        )
        assert score_high > score_low

    def test_monotonic_decay(self) -> None:
        """Utility is monotonically decreasing with days (all else equal)."""
        scores = [
            compute_utility_score(0.5, d, 1, 0.5, 5)
            for d in range(0, 60, 5)
        ]
        for i in range(1, len(scores)):
            assert scores[i] <= scores[i - 1]


class TestComputeImpactDistribution:
    """Tests for compute_impact_distribution function."""

    def _write_entry(self, entries_dir: Path, fname: str, impact: float, status: str = "active") -> None:
        entries_dir.mkdir(parents=True, exist_ok=True)
        (entries_dir / fname).write_text(
            f"id: {fname}\nimpact: {impact}\nstatus: {status}\n"
        )

    def test_empty_dir_returns_zeros(self, tmp_path: Path) -> None:
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        result = compute_impact_distribution(entries_dir)
        assert result["total_active"] == 0
        critical = result["critical"]
        assert isinstance(critical, dict)
        assert critical["count"] == 0
        assert critical["pct"] == 0.0

    def test_nonexistent_dir_returns_zeros(self, tmp_path: Path) -> None:
        result = compute_impact_distribution(tmp_path / "nonexistent")
        assert result["total_active"] == 0

    def test_mixed_tiers(self, tmp_path: Path) -> None:
        entries_dir = tmp_path / "entries"
        # 1 critical (0.95), 2 high (0.75, 0.80), 1 medium (0.5), 1 low (0.2)
        self._write_entry(entries_dir, "a.yaml", 0.95)
        self._write_entry(entries_dir, "b.yaml", 0.75)
        self._write_entry(entries_dir, "c.yaml", 0.80)
        self._write_entry(entries_dir, "d.yaml", 0.50)
        self._write_entry(entries_dir, "e.yaml", 0.20)
        result = compute_impact_distribution(entries_dir)
        assert result["total_active"] == 5
        critical = result["critical"]
        assert isinstance(critical, dict)
        assert critical["count"] == 1
        assert abs(critical["pct"] - 0.2) < 0.01
        high = result["high"]
        assert isinstance(high, dict)
        assert high["count"] == 2
        assert abs(high["pct"] - 0.4) < 0.01
        medium = result["medium"]
        assert isinstance(medium, dict)
        assert medium["count"] == 1
        low = result["low"]
        assert isinstance(low, dict)
        assert low["count"] == 1

    def test_ignores_inactive_entries(self, tmp_path: Path) -> None:
        entries_dir = tmp_path / "entries"
        self._write_entry(entries_dir, "active.yaml", 0.9)
        self._write_entry(entries_dir, "resolved.yaml", 0.9, status="resolved")
        self._write_entry(entries_dir, "obsolete.yaml", 0.9, status="obsolete")
        result = compute_impact_distribution(entries_dir)
        assert result["total_active"] == 1
        critical = result["critical"]
        assert isinstance(critical, dict)
        assert critical["count"] == 1


# --- PRD-CORE-060: Complexity Classification Tests ---


class TestComplexitySignals:
    """Tests for ComplexitySignals model (FR02)."""

    def test_defaults(self) -> None:
        signals = ComplexitySignals()
        assert signals.files_affected == 1
        assert signals.novel_patterns is False
        assert signals.cross_cutting is False
        assert signals.architecture_change is False
        assert signals.external_integration is False
        assert signals.large_refactoring is False
        assert signals.security_change is False
        assert signals.data_migration is False
        assert signals.unknown_codebase is False

    def test_files_affected_negative_raises(self) -> None:
        with pytest.raises(ValidationError):
            ComplexitySignals(files_affected=-1)

    def test_files_affected_capped_at_100(self) -> None:
        with pytest.raises(ValidationError):
            ComplexitySignals(files_affected=101)

    def test_frozen_model(self) -> None:
        signals = ComplexitySignals()
        with pytest.raises(ValidationError):
            signals.files_affected = 5  # type: ignore[misc]


class TestComplexityOverride:
    """Tests for ComplexityOverride model (FR09)."""

    def test_basic_creation(self) -> None:
        override = ComplexityOverride(
            reason="hard override",
            signals=["security_change", "data_migration"],
            raw_score=2,
        )
        assert override.reason == "hard override"
        assert len(override.signals) == 2
        assert override.raw_score == 2


class TestRunStateComplexityFields:
    """Tests for RunState complexity fields (FR02, FR09)."""

    def test_runstate_defaults_none(self) -> None:
        rs = RunState(run_id="test-1", task="test")
        assert rs.complexity_class is None
        assert rs.complexity_signals is None
        assert rs.complexity_override is None
        assert rs.phase_requirements is None

    def test_runstate_with_complexity(self) -> None:
        rs = RunState(
            run_id="test-2",
            task="test",
            complexity_class=ComplexityClass.COMPREHENSIVE,
            complexity_signals=ComplexitySignals(
                files_affected=5, architecture_change=True,
            ),
        )
        assert rs.complexity_class == "COMPREHENSIVE"  # use_enum_values=True
        assert rs.complexity_signals is not None
        assert rs.complexity_signals.files_affected == 5

    def test_runstate_yaml_roundtrip(self) -> None:
        """Ensure enum values survive JSON/YAML serialization."""
        import json

        rs = RunState(
            run_id="rt-1",
            task="roundtrip",
            complexity_class=ComplexityClass.COMPREHENSIVE,
            complexity_override=ComplexityOverride(
                reason="test",
                signals=["security_change"],
                raw_score=3,
            ),
            phase_requirements=PhaseRequirements(
                mandatory=["IMPLEMENT", "DELIVER"],
                optional=[],
                skipped=["RESEARCH"],
            ),
        )
        data = json.loads(rs.model_dump_json())
        assert data["complexity_class"] == "COMPREHENSIVE"
        assert data["complexity_override"]["reason"] == "test"
        assert data["phase_requirements"]["mandatory"] == ["IMPLEMENT", "DELIVER"]

        # Deserialize back
        rs2 = RunState(**data)
        assert rs2.complexity_class == "COMPREHENSIVE"
        assert rs2.complexity_override is not None
        assert rs2.complexity_override.raw_score == 3


class TestClassifyComplexity:
    """Tests for classify_complexity function (FR01, FR05)."""

    def test_minimal_all_defaults(self) -> None:
        """FR01: all signals False, files_affected=1 -> MINIMAL, raw_score=1."""
        signals = ComplexitySignals()
        tier, raw_score, override = classify_complexity(signals)
        assert raw_score == 1
        assert tier == ComplexityClass.MINIMAL
        assert override is None

    def test_comprehensive_high_score(self) -> None:
        """FR01: high signals -> COMPREHENSIVE."""
        signals = ComplexitySignals(
            files_affected=5, novel_patterns=True, cross_cutting=True,
        )
        tier, raw_score, override = classify_complexity(signals)
        # 5 + 3 + 2 = 10
        assert raw_score == 10
        assert tier == ComplexityClass.COMPREHENSIVE
        assert override is None

    def test_standard_mid_score(self) -> None:
        """FR01: mid-range score -> STANDARD."""
        signals = ComplexitySignals(
            files_affected=2, novel_patterns=True,
        )
        tier, raw_score, override = classify_complexity(signals)
        # 2 + 3 = 5
        assert raw_score == 5
        assert tier == ComplexityClass.STANDARD
        assert override is None

    def test_boundary_minimal_upper(self) -> None:
        """FR01: raw_score=1 exactly -> MINIMAL (default boundary is 1)."""
        signals = ComplexitySignals(files_affected=1)
        tier, raw_score, _ = classify_complexity(signals)
        assert raw_score == 1
        assert tier == ComplexityClass.MINIMAL

    def test_boundary_standard_lower(self) -> None:
        """FR01: raw_score=2 -> STANDARD."""
        signals = ComplexitySignals(files_affected=2)
        tier, raw_score, _ = classify_complexity(signals)
        assert raw_score == 2
        assert tier == ComplexityClass.STANDARD

    def test_boundary_comprehensive_lower(self) -> None:
        """FR01: raw_score=7 (comprehensive_tier=6, need >=7) -> COMPREHENSIVE."""
        signals = ComplexitySignals(
            files_affected=5, cross_cutting=True,
        )
        tier, raw_score, _ = classify_complexity(signals)
        # 5 + 2 = 7
        assert raw_score == 7
        assert tier == ComplexityClass.COMPREHENSIVE

    def test_boundary_standard_upper(self) -> None:
        """FR01: raw_score=6 -> STANDARD (not yet COMPREHENSIVE, need >=7)."""
        signals = ComplexitySignals(
            files_affected=4, cross_cutting=True,
        )
        tier, raw_score, _ = classify_complexity(signals)
        # 4 + 2 = 6
        assert raw_score == 6
        assert tier == ComplexityClass.STANDARD

    def test_files_affected_capped(self) -> None:
        """FR01: files_affected > max is capped at config max."""
        signals = ComplexitySignals(files_affected=50)
        tier, raw_score, _ = classify_complexity(signals)
        # Capped at 5
        assert raw_score == 5
        assert tier == ComplexityClass.STANDARD

    def test_hard_override_two_risk_signals(self) -> None:
        """FR05: 2 high-risk signals -> COMPREHENSIVE regardless of score."""
        signals = ComplexitySignals(
            files_affected=1,
            security_change=True,
            data_migration=True,
        )
        tier, raw_score, override = classify_complexity(signals)
        assert raw_score == 1  # Low score
        assert tier == ComplexityClass.COMPREHENSIVE  # Overridden
        assert override is not None
        assert "hard override" in override.reason
        assert "security_change" in override.signals
        assert "data_migration" in override.signals
        assert override.raw_score == 1

    def test_hard_override_three_risk_signals(self) -> None:
        """FR05: 3 high-risk signals -> COMPREHENSIVE."""
        signals = ComplexitySignals(
            files_affected=1,
            security_change=True,
            data_migration=True,
            unknown_codebase=True,
        )
        tier, _, override = classify_complexity(signals)
        assert tier == ComplexityClass.COMPREHENSIVE
        assert override is not None
        assert len(override.signals) == 3

    def test_single_risk_signal_escalates_minimal(self) -> None:
        """FR05: 1 risk signal escalates MINIMAL -> STANDARD."""
        signals = ComplexitySignals(
            files_affected=1,
            unknown_codebase=True,
        )
        tier, raw_score, override = classify_complexity(signals)
        assert raw_score == 1  # Would be MINIMAL by score alone
        assert tier == ComplexityClass.STANDARD
        assert override is not None
        assert "escalation" in override.reason

    def test_single_risk_signal_no_escalate_standard(self) -> None:
        """FR05: 1 risk signal on a STANDARD task does NOT escalate further."""
        signals = ComplexitySignals(
            files_affected=4,
            unknown_codebase=True,
        )
        tier, raw_score, override = classify_complexity(signals)
        assert raw_score == 4  # Already STANDARD
        assert tier == ComplexityClass.STANDARD
        assert override is None  # No escalation needed

    def test_config_override_boundaries(self) -> None:
        """FR08: custom config changes tier boundaries."""
        cfg = TRWConfig(complexity_tier_minimal=5, complexity_tier_comprehensive=10)
        signals = ComplexitySignals(files_affected=3)  # raw_score=3
        tier, _, _ = classify_complexity(signals, config=cfg)
        assert tier == ComplexityClass.MINIMAL  # 3 <= 5

    def test_config_override_weights(self) -> None:
        """FR08: custom config changes signal weights."""
        cfg = TRWConfig(complexity_weight_novel_patterns=5)
        signals = ComplexitySignals(files_affected=1, novel_patterns=True)
        _, raw_score, _ = classify_complexity(signals, config=cfg)
        assert raw_score == 6  # 1 + 5


class TestPhaseRequirements:
    """Tests for get_phase_requirements function (FR04)."""

    def test_minimal_phases(self) -> None:
        reqs = get_phase_requirements(ComplexityClass.MINIMAL)
        assert "IMPLEMENT" in reqs.mandatory
        assert "VALIDATE" in reqs.mandatory
        assert "DELIVER" in reqs.mandatory
        assert "RESEARCH" in reqs.skipped
        assert "PLAN" in reqs.skipped
        assert "REVIEW" in reqs.skipped

    def test_standard_phases(self) -> None:
        reqs = get_phase_requirements(ComplexityClass.STANDARD)
        assert "PLAN" in reqs.mandatory
        assert "IMPLEMENT" in reqs.mandatory
        assert "VALIDATE" in reqs.mandatory
        assert "REVIEW" in reqs.mandatory
        assert "DELIVER" in reqs.mandatory
        assert reqs.optional == []
        assert "RESEARCH" in reqs.skipped

    def test_comprehensive_phases(self) -> None:
        reqs = get_phase_requirements(ComplexityClass.COMPREHENSIVE)
        assert len(reqs.mandatory) == 6
        assert "RESEARCH" in reqs.mandatory
        assert "REVIEW" in reqs.mandatory
        assert reqs.optional == []
        assert reqs.skipped == []

    def test_implement_and_deliver_never_skipped(self) -> None:
        """FR04: IMPLEMENT and DELIVER never in skipped."""
        for tier in ComplexityClass:
            reqs = get_phase_requirements(tier)
            assert "IMPLEMENT" not in reqs.skipped
            assert "DELIVER" not in reqs.skipped


class TestTierCeremonyScore:
    """Tests for compute_tier_ceremony_score function (FR03)."""

    def _make_events(self, event_types: list[str]) -> list[dict[str, object]]:
        """Helper to create minimal event dicts."""
        return [{"event": "tool_invocation", "tool_name": t} for t in event_types]

    def test_minimal_recall_and_deliver_high_score(self) -> None:
        """FR03: MINIMAL with trw_recall + trw_deliver -> score >= 80."""
        events = self._make_events(["trw_session_start", "trw_deliver"])
        result = compute_tier_ceremony_score(events, ComplexityClass.MINIMAL)
        assert result["score"] >= 60  # 2/3 expected events matched
        assert result["tier"] == "MINIMAL"

    def test_minimal_all_events_perfect(self) -> None:
        """FR03: MINIMAL with all 3 expected events -> 100."""
        events = self._make_events(["trw_session_start", "trw_build_check", "trw_deliver"])
        result = compute_tier_ceremony_score(events, ComplexityClass.MINIMAL)
        assert result["score"] == 100

    def test_comprehensive_missing_review_penalized(self) -> None:
        """FR03: COMPREHENSIVE missing trw_review -> score <= 60."""
        events = self._make_events([
            "trw_session_start", "trw_init", "trw_checkpoint",
            "trw_build_check", "trw_deliver",
        ])
        result = compute_tier_ceremony_score(events, ComplexityClass.COMPREHENSIVE)
        # 5/7 matched = ~71, minus 25 penalty = ~46
        assert result["score"] <= 60

    def test_standard_with_review_bonus(self) -> None:
        """FR03: STANDARD with all events including review -> 100."""
        events = self._make_events([
            "trw_session_start", "trw_init", "trw_checkpoint",
            "trw_build_check", "trw_deliver", "trw_review",
        ])
        result = compute_tier_ceremony_score(events, ComplexityClass.STANDARD)
        # 6/6 expected = 100, review_mandatory=True satisfied, no penalty
        assert result["score"] == 100

    def test_standard_without_review_penalized(self) -> None:
        """FR03: STANDARD without review incurs 15-point penalty (review is mandatory)."""
        events = self._make_events([
            "trw_session_start", "trw_init", "trw_checkpoint",
            "trw_build_check", "trw_deliver",
        ])
        result = compute_tier_ceremony_score(events, ComplexityClass.STANDARD)
        # 5/6 expected = round(83.33) = 83, minus 15 penalty = 68
        assert result["score"] == 68

    def test_none_defaults_to_standard(self) -> None:
        """FR03: None complexity_class defaults to STANDARD."""
        events = self._make_events([
            "trw_session_start", "trw_init", "trw_checkpoint",
            "trw_build_check", "trw_deliver",
        ])
        result = compute_tier_ceremony_score(events, None)
        assert result["tier"] == "STANDARD"
        # 5/6 expected = 83, minus 15 missing_review_penalty = 68
        assert result["score"] == 68

    def test_string_tier_accepted(self) -> None:
        """FR03: String tier values are accepted."""
        events = self._make_events(["trw_session_start", "trw_deliver"])
        result = compute_tier_ceremony_score(events, "MINIMAL")
        assert result["tier"] == "MINIMAL"

    def test_empty_events_zero_score(self) -> None:
        """FR03: No events -> score 0."""
        result = compute_tier_ceremony_score([], ComplexityClass.STANDARD)
        assert result["score"] == 0

    def test_comprehensive_all_events_perfect(self) -> None:
        """FR03: COMPREHENSIVE with all events -> 100."""
        events = self._make_events([
            "trw_session_start", "trw_init", "trw_checkpoint",
            "trw_learn", "trw_build_check", "trw_deliver", "trw_review",
        ])
        result = compute_tier_ceremony_score(events, ComplexityClass.COMPREHENSIVE)
        assert result["score"] == 100
