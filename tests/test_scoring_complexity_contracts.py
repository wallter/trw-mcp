"""Tests for scoring complexity classification and phase contracts."""

from __future__ import annotations

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.run import ComplexityClass, ComplexitySignals
from trw_mcp.scoring import classify_complexity, get_ceremony_depth_contract, get_phase_requirements


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
            files_affected=5,
            novel_patterns=True,
            cross_cutting=True,
        )
        tier, raw_score, override = classify_complexity(signals)
        # 5 + 3 + 2 = 10
        assert raw_score == 10
        assert tier == ComplexityClass.COMPREHENSIVE
        assert override is None

    def test_standard_mid_score(self) -> None:
        """FR01: mid-range score -> STANDARD."""
        signals = ComplexitySignals(
            files_affected=2,
            novel_patterns=True,
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
            files_affected=5,
            cross_cutting=True,
        )
        tier, raw_score, _ = classify_complexity(signals)
        # 5 + 2 = 7
        assert raw_score == 7
        assert tier == ComplexityClass.COMPREHENSIVE

    def test_boundary_standard_upper(self) -> None:
        """FR01: raw_score=6 -> STANDARD (not yet COMPREHENSIVE, need >=7)."""
        signals = ComplexitySignals(
            files_affected=4,
            cross_cutting=True,
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


class TestCeremonyDepthContract:
    """PRD-CORE-155: normalized tier-to-depth contract."""

    def test_minimal_depth_keeps_validate_mandatory(self) -> None:
        contract = get_ceremony_depth_contract(ComplexityClass.MINIMAL)

        assert contract.ceremony_depth == "light"
        assert contract.trace_depth == "minimal"
        assert contract.nudge_policy == "sparse"
        assert contract.validation_required is True
        assert "VALIDATE" in contract.mandatory_phases
        assert "IMPLEMENT" in contract.mandatory_phases
        assert "DELIVER" in contract.mandatory_phases

    def test_standard_depth_matches_standard_phase_contract(self) -> None:
        contract = get_ceremony_depth_contract(ComplexityClass.STANDARD)

        assert contract.ceremony_depth == "standard"
        assert contract.trace_depth == "standard"
        assert contract.nudge_policy == "standard"
        assert contract.mandatory_phases == tuple(get_phase_requirements(ComplexityClass.STANDARD).mandatory)

    def test_comprehensive_depth_is_causal_and_all_phases(self) -> None:
        contract = get_ceremony_depth_contract(ComplexityClass.COMPREHENSIVE)

        assert contract.ceremony_depth == "comprehensive"
        assert contract.trace_depth == "causal"
        assert contract.nudge_policy == "dense"
        assert contract.mandatory_phases == tuple(get_phase_requirements(ComplexityClass.COMPREHENSIVE).mandatory)
