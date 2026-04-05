"""Tests for bandit-based learning selection policy (PRD-CORE-105 FR03/FR04/FR06).

Covers:
- WithholdingPolicy tiered withholding rates
- resolve_client_class mapping
- select_nudge_learning_bandit selection with burst and withholding
- Forced re-evaluation triggers
"""

from __future__ import annotations

import random
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_candidate(
    lid: str,
    impact: float = 0.8,
    protection_tier: str = "normal",
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    """Create a minimal learning candidate dict."""
    result: dict[str, object] = {
        "id": lid,
        "summary": f"Learning {lid}",
        "impact": impact,
        "protection_tier": protection_tier,
    }
    if metadata is not None:
        result["metadata"] = metadata
    return result


# ---------------------------------------------------------------------------
# resolve_client_class
# ---------------------------------------------------------------------------


class TestResolveClientClass:
    def test_resolve_client_class_full_mode(self) -> None:
        """claude-code maps to full_mode."""
        from trw_mcp.state.bandit_policy import resolve_client_class

        assert resolve_client_class("claude-code") == "full_mode"

    def test_resolve_client_class_full_mode_cursor(self) -> None:
        """cursor also maps to full_mode."""
        from trw_mcp.state.bandit_policy import resolve_client_class

        assert resolve_client_class("cursor") == "full_mode"

    def test_resolve_client_class_light_mode(self) -> None:
        """opencode maps to light_mode."""
        from trw_mcp.state.bandit_policy import resolve_client_class

        assert resolve_client_class("opencode") == "light_mode"

    def test_resolve_client_class_light_mode_codex(self) -> None:
        """codex maps to light_mode."""
        from trw_mcp.state.bandit_policy import resolve_client_class

        assert resolve_client_class("codex") == "light_mode"

    def test_resolve_client_class_unknown_default(self) -> None:
        """Unknown client profile defaults to full_mode."""
        from trw_mcp.state.bandit_policy import resolve_client_class

        assert resolve_client_class("some-unknown-client") == "full_mode"
        assert resolve_client_class("") == "full_mode"


# ---------------------------------------------------------------------------
# WithholdingPolicy — tiered rates
# ---------------------------------------------------------------------------


class TestWithholdingPolicyCritical:
    def test_critical_tier_never_withheld(self) -> None:
        """Critical tier: over 1000 calls, withhold count must be 0."""
        from trw_mcp.state.bandit_policy import WithholdingPolicy

        policy = WithholdingPolicy(client_class="full_mode")
        candidate = _make_candidate("L-crit", protection_tier="critical")

        withheld_count = sum(
            1 for _ in range(1000) if policy.should_withhold(candidate)
        )
        assert withheld_count == 0


class TestWithholdingPolicyNormal:
    def test_normal_tier_withholding_rate_full_mode(self) -> None:
        """Normal tier full_mode: over 1000 calls, rate should be 12-25%."""
        from trw_mcp.state.bandit_policy import WithholdingPolicy

        policy = WithholdingPolicy(client_class="full_mode")
        candidate = _make_candidate("L-norm", protection_tier="normal")

        withheld_count = sum(
            1 for _ in range(1000) if policy.should_withhold(candidate)
        )
        rate = withheld_count / 1000
        # Allow slight statistical margin
        assert 0.08 <= rate <= 0.32, f"Expected 12-25% range, got {rate:.2%}"

    def test_normal_tier_withholding_rate_light_mode(self) -> None:
        """Normal tier light_mode: over 1000 calls, rate should be 20-35%."""
        from trw_mcp.state.bandit_policy import WithholdingPolicy

        policy = WithholdingPolicy(client_class="light_mode")
        candidate = _make_candidate("L-norm-light", protection_tier="normal")

        withheld_count = sum(
            1 for _ in range(1000) if policy.should_withhold(candidate)
        )
        rate = withheld_count / 1000
        # Allow slight statistical margin
        assert 0.15 <= rate <= 0.42, f"Expected 20-35% range, got {rate:.2%}"


class TestWithholdingPolicyHigh:
    def test_high_tier_withholding_rate(self) -> None:
        """High tier: over 1000 calls, rate should be ~5%."""
        from trw_mcp.state.bandit_policy import WithholdingPolicy

        policy = WithholdingPolicy(client_class="full_mode")
        candidate = _make_candidate("L-high", protection_tier="high")

        withheld_count = sum(
            1 for _ in range(1000) if policy.should_withhold(candidate)
        )
        rate = withheld_count / 1000
        # High tier has fixed 5% floor and ceiling, allow statistical margin
        assert 0.02 <= rate <= 0.09, f"Expected ~5% rate, got {rate:.2%}"


class TestWithholdingPolicyLow:
    def test_low_tier_withholding_rate(self) -> None:
        """Low tier: over 1000 calls, rate should be 30-50%."""
        from trw_mcp.state.bandit_policy import WithholdingPolicy

        policy = WithholdingPolicy(client_class="full_mode")
        candidate = _make_candidate("L-low", protection_tier="low")

        withheld_count = sum(
            1 for _ in range(1000) if policy.should_withhold(candidate)
        )
        rate = withheld_count / 1000
        assert 0.25 <= rate <= 0.58, f"Expected 30-50% range, got {rate:.2%}"


# ---------------------------------------------------------------------------
# Forced re-evaluation triggers
# ---------------------------------------------------------------------------


class TestForcedTriggers:
    def test_forced_trigger_consecutive_shown(self) -> None:
        """Learning shown >20 consecutive sessions without withholding -> force trial."""
        from trw_mcp.state.bandit_policy import WithholdingPolicy

        policy = WithholdingPolicy(client_class="full_mode", force_trial_threshold=20)
        # Critical tier normally never withheld, but forced trigger overrides
        candidate = _make_candidate(
            "L-stale",
            protection_tier="critical",
            metadata={"consecutive_shown": 25},
        )

        # With forced trigger on critical, it should use normal-tier rate
        # Over 1000 trials, we should see SOME withholding (12-25% for full_mode normal)
        withheld_count = sum(
            1 for _ in range(1000) if policy.should_withhold(candidate)
        )
        rate = withheld_count / 1000
        assert rate > 0.05, f"Forced trigger should cause withholding, got {rate:.2%}"

    def test_forced_trigger_anchor_validity_drop(self) -> None:
        """Anchor validity dropped by >0.3 -> force trial."""
        from trw_mcp.state.bandit_policy import WithholdingPolicy

        policy = WithholdingPolicy(client_class="full_mode")
        candidate = _make_candidate(
            "L-drift",
            protection_tier="critical",
            metadata={"prev_anchor_validity": 0.2},
        )

        withheld_count = sum(
            1 for _ in range(1000) if policy.should_withhold(candidate)
        )
        rate = withheld_count / 1000
        assert rate > 0.05, f"Anchor validity trigger should cause withholding, got {rate:.2%}"

    def test_forced_trigger_expired_workaround(self) -> None:
        """Workaround type past expires date -> force trial."""
        from trw_mcp.state.bandit_policy import WithholdingPolicy

        policy = WithholdingPolicy(client_class="full_mode")
        candidate = _make_candidate(
            "L-expired",
            protection_tier="critical",
            metadata={"type": "workaround", "expires": "2020-01-01"},
        )

        withheld_count = sum(
            1 for _ in range(1000) if policy.should_withhold(candidate)
        )
        rate = withheld_count / 1000
        assert rate > 0.05, f"Expired workaround trigger should cause withholding, got {rate:.2%}"

    def test_no_forced_trigger_below_threshold(self) -> None:
        """consecutive_shown below threshold should not trigger forced trial."""
        from trw_mcp.state.bandit_policy import WithholdingPolicy

        policy = WithholdingPolicy(client_class="full_mode", force_trial_threshold=20)
        candidate = _make_candidate(
            "L-ok",
            protection_tier="critical",
            metadata={"consecutive_shown": 10},
        )

        withheld_count = sum(
            1 for _ in range(1000) if policy.should_withhold(candidate)
        )
        assert withheld_count == 0, "Critical tier without forced trigger should never withhold"


# ---------------------------------------------------------------------------
# select_nudge_learning_bandit — selection behavior
# ---------------------------------------------------------------------------


class TestSelectNudgeLearningBandit:
    def test_select_nudge_learning_empty_candidates(self) -> None:
        """Empty candidate list returns ([], False)."""
        from trw_mcp.state.bandit_policy import select_nudge_learning_bandit

        bandit = MagicMock()
        from trw_mcp.state.bandit_policy import WithholdingPolicy

        policy = WithholdingPolicy()

        result, is_transition = select_nudge_learning_bandit(
            candidates=[],
            bandit=bandit,
            policy=policy,
            phase="implement",
            previous_phase="implement",
        )
        assert result == []
        assert is_transition is False

    def test_select_nudge_learning_single(self) -> None:
        """Non-transition selects exactly 1 learning."""
        from trw_mcp.state.bandit_policy import (
            WithholdingPolicy,
            select_nudge_learning_bandit,
        )
        from trw_memory.bandit import BanditDecision, BanditSelector

        bandit = BanditSelector(cold_start_min=0, floor_exploration=0.0)
        # Pre-seed the bandit so it has arms
        for lid in ["L-a", "L-b", "L-c"]:
            bandit.update(lid, 0.5)

        policy = WithholdingPolicy(client_class="full_mode")
        candidates = [
            _make_candidate("L-a", protection_tier="critical"),
            _make_candidate("L-b", protection_tier="critical"),
            _make_candidate("L-c", protection_tier="critical"),
        ]

        result, is_transition = select_nudge_learning_bandit(
            candidates=candidates,
            bandit=bandit,
            policy=policy,
            phase="implement",
            previous_phase="implement",  # same phase = no transition
        )
        assert len(result) == 1
        assert is_transition is False

    def test_select_nudge_learning_phase_burst(self) -> None:
        """Phase transition selects 2-3 learnings (burst)."""
        from trw_mcp.state.bandit_policy import (
            WithholdingPolicy,
            select_nudge_learning_bandit,
        )
        from trw_memory.bandit import BanditSelector

        bandit = BanditSelector(cold_start_min=0, floor_exploration=0.0)
        candidates = []
        for i in range(5):
            lid = f"L-{i}"
            bandit.update(lid, 0.5)
            candidates.append(_make_candidate(lid, protection_tier="critical"))

        policy = WithholdingPolicy(client_class="full_mode")

        result, is_transition = select_nudge_learning_bandit(
            candidates=candidates,
            bandit=bandit,
            policy=policy,
            phase="validate",
            previous_phase="implement",  # different phase = transition
        )
        assert 2 <= len(result) <= 3
        assert is_transition is True

    def test_phase_transition_detection_no_previous(self) -> None:
        """First phase (no previous_phase) is not a transition."""
        from trw_mcp.state.bandit_policy import (
            WithholdingPolicy,
            select_nudge_learning_bandit,
        )
        from trw_memory.bandit import BanditSelector

        bandit = BanditSelector(cold_start_min=0, floor_exploration=0.0)
        bandit.update("L-a", 0.5)
        candidates = [_make_candidate("L-a", protection_tier="critical")]

        policy = WithholdingPolicy(client_class="full_mode")

        result, is_transition = select_nudge_learning_bandit(
            candidates=candidates,
            bandit=bandit,
            policy=policy,
            phase="implement",
            previous_phase="",  # empty = first phase
        )
        assert len(result) == 1
        assert is_transition is False

    def test_phase_transition_withholding(self) -> None:
        """At phase transition, non-critical learnings can still be withheld."""
        from trw_mcp.state.bandit_policy import (
            WithholdingPolicy,
            select_nudge_learning_bandit,
        )
        from trw_memory.bandit import BanditSelector

        bandit = BanditSelector(cold_start_min=0, floor_exploration=0.0)
        # Use normal-tier (12-25% withholding) candidates
        candidates = []
        for i in range(10):
            lid = f"L-{i}"
            bandit.update(lid, 0.5)
            candidates.append(_make_candidate(lid, protection_tier="normal"))

        policy = WithholdingPolicy(client_class="full_mode")

        # Run many trials to check that withholding sometimes happens at transition
        total_selected = 0
        max_possible = 0
        for _ in range(100):
            result, is_transition = select_nudge_learning_bandit(
                candidates=candidates,
                bandit=bandit,
                policy=policy,
                phase="validate",
                previous_phase="implement",
            )
            total_selected += len(result)
            max_possible += 3  # burst selects up to 3

        # Some should have been withheld (normal tier: 12-25%)
        # But we should still see most selected (not all withheld)
        assert total_selected > 0, "Should select at least some learnings"
        assert total_selected < max_possible, "Some should be withheld"

    def test_withheld_candidate_replaced_by_runner_up(self) -> None:
        """When a selected candidate is withheld, the bandit tries the runner-up."""
        from trw_mcp.state.bandit_policy import (
            WithholdingPolicy,
            select_nudge_learning_bandit,
        )
        from trw_memory.bandit import BanditSelector

        bandit = BanditSelector(cold_start_min=0, floor_exploration=0.0)
        # Give L-safe many high rewards so it's the runner-up
        for _ in range(10):
            bandit.update("L-safe", 0.9)
            bandit.update("L-risky", 0.8)

        # L-risky is low tier (30-50% withholding), L-safe is critical (0% withholding)
        candidates = [
            _make_candidate("L-risky", protection_tier="low"),
            _make_candidate("L-safe", protection_tier="critical"),
        ]

        policy = WithholdingPolicy(client_class="full_mode")

        # Run many times; some should select L-safe when L-risky is withheld
        selected_ids: list[str] = []
        for _ in range(100):
            result, _ = select_nudge_learning_bandit(
                candidates=candidates,
                bandit=bandit,
                policy=policy,
                phase="implement",
                previous_phase="implement",
            )
            for r in result:
                selected_ids.append(str(r.get("id", "")))

        # We should see both IDs in the results across trials
        unique_ids = set(selected_ids)
        assert "L-safe" in unique_ids, "Runner-up should be selected when primary is withheld"


# ---------------------------------------------------------------------------
# build_context_vector
# ---------------------------------------------------------------------------


class TestBuildContextVector:
    def test_build_context_vector_dimension(self) -> None:
        """Context vector must be exactly 21 dimensions."""
        from trw_mcp.state.bandit_policy import ENGINEERING_CONTEXT_DIM, build_context_vector

        vec = build_context_vector(
            phase="implement",
            agent_type="implementer",
            task_type="feature",
            session_progress=0.5,
            domain_similarity=0.8,
            files_count=10,
        )
        assert len(vec) == ENGINEERING_CONTEXT_DIM
        assert all(isinstance(v, float) for v in vec)

    def test_build_context_vector_different_phases(self) -> None:
        """Different phases produce different one-hot encodings."""
        from trw_mcp.state.bandit_policy import build_context_vector

        vec_impl = build_context_vector(phase="implement")
        vec_val = build_context_vector(phase="validate")
        assert vec_impl != vec_val

    def test_build_context_vector_unknown_phase(self) -> None:
        """Unknown phase still produces a valid 21-dim vector (all zeros in phase slots)."""
        from trw_mcp.state.bandit_policy import ENGINEERING_CONTEXT_DIM, build_context_vector

        vec = build_context_vector(phase="unknown_phase")
        assert len(vec) == ENGINEERING_CONTEXT_DIM

    def test_build_context_vector_files_clamped(self) -> None:
        """Files count should be normalized (clamped to [0, 1])."""
        from trw_mcp.state.bandit_policy import build_context_vector

        vec = build_context_vector(files_count=500)
        # The files_count normalized value should be clamped at 1.0
        assert all(0.0 <= v <= 1.0 for v in vec)


# ---------------------------------------------------------------------------
# Integration with _nudge_rules.select_nudge_learning
# ---------------------------------------------------------------------------


class TestNudgeRulesIntegration:
    def test_select_nudge_learning_fallback_without_bandit(self) -> None:
        """Without bandit state, select_nudge_learning falls back to deterministic ranking."""
        from trw_mcp.state._nudge_rules import select_nudge_learning
        from trw_mcp.state._nudge_state import CeremonyState

        state = CeremonyState(phase="implement")
        candidates: list[dict[str, object]] = [
            {"id": "L-a", "summary": "First"},
            {"id": "L-b", "summary": "Second"},
        ]

        selected, is_fallback = select_nudge_learning(state, candidates, "implement")
        assert selected is not None
        assert str(selected.get("id", "")) == "L-a"
        assert is_fallback is False

    def test_select_nudge_learning_with_bandit_param(self) -> None:
        """When bandit param is provided, uses bandit-based selection."""
        from trw_mcp.state._nudge_rules import select_nudge_learning
        from trw_mcp.state._nudge_state import CeremonyState
        from trw_memory.bandit import BanditSelector

        state = CeremonyState(phase="implement")
        bandit = BanditSelector(cold_start_min=0, floor_exploration=0.0)

        candidates: list[dict[str, object]] = [
            {"id": "L-a", "summary": "First", "protection_tier": "critical"},
            {"id": "L-b", "summary": "Second", "protection_tier": "critical"},
        ]
        for c in candidates:
            bandit.update(str(c["id"]), 0.5)

        selected, is_fallback = select_nudge_learning(
            state,
            candidates,
            "implement",
            bandit=bandit,
        )
        assert selected is not None
        # Should return a valid candidate
        assert str(selected.get("id", "")) in {"L-a", "L-b"}
