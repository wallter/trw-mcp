"""Integration tests for bandit-based learning selection policy (PRD-CORE-105).

Exercises real BanditSelector instances (not mocked) together with
WithholdingPolicy, phase-transition burst logic, context vector
construction, and bandit state serialization roundtrips.
"""

from __future__ import annotations

import random

import pytest

from trw_memory.bandit import BanditSelector

from trw_mcp.state.bandit_policy import (
    ENGINEERING_CONTEXT_DIM,
    WithholdingPolicy,
    build_context_vector,
    select_nudge_learning_bandit,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_candidate(
    lid: str,
    protection_tier: str = "normal",
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    """Create a minimal learning candidate dict."""
    result: dict[str, object] = {
        "id": lid,
        "summary": f"Learning {lid}",
        "impact": 0.8,
        "protection_tier": protection_tier,
    }
    if metadata is not None:
        result["metadata"] = metadata
    return result


# ---------------------------------------------------------------------------
# Test 1: Withholding rates — statistical verification
# ---------------------------------------------------------------------------


class TestWithholdingRatesStatistical:
    """Verify withholding rates across protection tiers with real randomness."""

    def test_withholding_rates_statistical(self) -> None:
        """Run 1000 iterations per tier and verify rate ranges.

        - critical: NEVER withheld (rate = 0.0)
        - high: rarely withheld (rate < 0.10)
        - normal: withheld 12-25% of the time
        - low: withheld >= 25% of the time
        """
        rng = random.Random(42)
        random.seed(42)

        policy = WithholdingPolicy(client_class="full_mode")
        iterations = 1000

        tiers = {
            "critical": _make_candidate("L-crit", protection_tier="critical"),
            "high": _make_candidate("L-high", protection_tier="high"),
            "normal": _make_candidate("L-norm", protection_tier="normal"),
            "low": _make_candidate("L-low", protection_tier="low"),
        }

        results: dict[str, float] = {}
        for tier_name, candidate in tiers.items():
            withheld = sum(
                1 for _ in range(iterations) if policy.should_withhold(candidate)
            )
            results[tier_name] = withheld / iterations

        # Critical: NEVER withheld
        assert results["critical"] == 0.0, (
            f"Critical tier should never be withheld, got rate={results['critical']:.3f}"
        )

        # High: rarely withheld (fixed rate 5%, allow statistical margin)
        assert results["high"] < 0.10, (
            f"High tier should be rarely withheld (<10%), got rate={results['high']:.3f}"
        )

        # Normal: withheld 12-25% (allow statistical margin)
        assert 0.08 <= results["normal"] <= 0.32, (
            f"Normal tier should be withheld 12-25%, got rate={results['normal']:.3f}"
        )

        # Low: withheld >= 25% (floor is 30%)
        assert results["low"] >= 0.20, (
            f"Low tier should be withheld >= 25%, got rate={results['low']:.3f}"
        )


# ---------------------------------------------------------------------------
# Test 2: Phase transition burst
# ---------------------------------------------------------------------------


class TestPhaseTransitionBurst:
    """Verify burst selection at phase transitions."""

    def test_phase_transition_burst(self) -> None:
        """At a phase transition (plan -> implement), 2-3 learnings
        should be selected (burst behavior), not just 1.
        """
        random.seed(123)

        bandit = BanditSelector(cold_start_min=0, floor_exploration=0.0)
        candidates = []
        for i in range(5):
            lid = f"L-{i}"
            bandit.update(lid, 0.5)
            candidates.append(_make_candidate(lid, protection_tier="critical"))

        policy = WithholdingPolicy(client_class="full_mode")

        selected, is_transition = select_nudge_learning_bandit(
            candidates=candidates,
            bandit=bandit,
            policy=policy,
            phase="implement",
            previous_phase="plan",
        )

        assert is_transition is True
        assert 2 <= len(selected) <= 3, (
            f"Phase transition should select 2-3 learnings, got {len(selected)}"
        )

        # All selected should be from the candidate pool
        selected_ids = {str(s["id"]) for s in selected}
        candidate_ids = {str(c["id"]) for c in candidates}
        assert selected_ids.issubset(candidate_ids)

        # No duplicates
        assert len(selected_ids) == len(selected)


# ---------------------------------------------------------------------------
# Test 3: No transition — single learning selected
# ---------------------------------------------------------------------------


class TestNoTransitionSingleLearning:
    """Verify single learning selection when no phase transition occurs."""

    def test_no_transition_single_learning(self) -> None:
        """Same phase (implement -> implement) should select exactly 1 learning."""
        random.seed(456)

        bandit = BanditSelector(cold_start_min=0, floor_exploration=0.0)
        candidates = []
        for i in range(5):
            lid = f"L-{i}"
            bandit.update(lid, 0.5)
            candidates.append(_make_candidate(lid, protection_tier="critical"))

        policy = WithholdingPolicy(client_class="full_mode")

        selected, is_transition = select_nudge_learning_bandit(
            candidates=candidates,
            bandit=bandit,
            policy=policy,
            phase="implement",
            previous_phase="implement",
        )

        assert is_transition is False
        assert len(selected) == 1, (
            f"No phase transition should select exactly 1, got {len(selected)}"
        )

        # Selected learning should be from the candidate pool
        selected_id = str(selected[0]["id"])
        candidate_ids = {str(c["id"]) for c in candidates}
        assert selected_id in candidate_ids


# ---------------------------------------------------------------------------
# Test 4: Context vector dimensions
# ---------------------------------------------------------------------------


class TestContextVectorDimensions:
    """Verify context vector structure and one-hot encoding."""

    def test_context_vector_dimensions(self) -> None:
        """build_context_vector with phase='implement', agent_type='lead',
        task_type='bugfix' produces a 21-dim vector with correct one-hot slots.
        """
        vec = build_context_vector(
            phase="implement",
            agent_type="lead",
            task_type="bugfix",
        )

        # Must be exactly 21 dimensions
        assert len(vec) == ENGINEERING_CONTEXT_DIM
        assert len(vec) == 21

        # All values should be valid floats in [0.0, 1.0]
        for i, v in enumerate(vec):
            assert isinstance(v, float), f"Index {i} is not a float: {type(v)}"
            assert 0.0 <= v <= 1.0, f"Index {i} out of range: {v}"

        # Phase one-hot: indices 0-5
        # Phases: research(0), plan(1), implement(2), validate(3), review(4), deliver(5)
        assert vec[2] == 1.0, "implement slot (index 2) should be 1.0"
        assert vec[0] == 0.0, "research slot should be 0.0"
        assert vec[1] == 0.0, "plan slot should be 0.0"
        assert vec[3] == 0.0, "validate slot should be 0.0"
        assert vec[4] == 0.0, "review slot should be 0.0"
        assert vec[5] == 0.0, "deliver slot should be 0.0"

        # Agent type one-hot: indices 6-9 (4 types per PRD-CORE-105)
        # Types: orchestrator(6), implementer(7), tester(8), reviewer(9)
        # "lead" resolves to "orchestrator" via alias
        assert vec[6] == 1.0, "orchestrator/lead slot (index 6) should be 1.0"
        assert vec[7] == 0.0, "implementer slot should be 0.0"
        assert vec[8] == 0.0, "tester slot should be 0.0"
        assert vec[9] == 0.0, "reviewer slot should be 0.0"

        # Task type one-hot: indices 10-15 (6 types per PRD-CORE-105)
        # Types: feature(10), bugfix(11), refactor(12), infrastructure(13), docs(14), investigation(15)
        assert vec[11] == 1.0, "bugfix slot (index 11) should be 1.0"
        assert vec[10] == 0.0, "feature slot should be 0.0"
        assert vec[12] == 0.0, "refactor slot should be 0.0"
        assert vec[13] == 0.0, "infrastructure slot should be 0.0"
        assert vec[14] == 0.0, "docs slot should be 0.0"
        assert vec[15] == 0.0, "investigation slot should be 0.0"

        # Remaining dims (16-20) should be 0.0 (defaults)
        assert vec[16] == 0.0, "session_progress default should be 0.0"
        assert vec[17] == 0.0, "domain_similarity default should be 0.0"
        assert vec[18] == 0.0, "files_count default should be 0.0"
        assert vec[19] == 0.0, "reserved dim should be 0.0"
        assert vec[20] == 0.0, "reserved dim should be 0.0"


# ---------------------------------------------------------------------------
# Test 5: Bandit state persistence roundtrip
# ---------------------------------------------------------------------------


class TestBanditStatePersistenceRoundtrip:
    """Verify bandit state survives serialization and deserialization."""

    def test_bandit_state_persistence_roundtrip(self) -> None:
        """Create a BanditSelector with tau=30, add 5 arms, run 10 select/update
        cycles. Serialize to JSON. Deserialize into a new BanditSelector. Run
        10 more cycles. Verify total observation count = 20, arm IDs preserved,
        and posterior parameters are non-default.
        """
        random.seed(789)

        arm_ids = ["arm-A", "arm-B", "arm-C", "arm-D", "arm-E"]

        # Phase 1: Create bandit and run 10 cycles
        bandit1 = BanditSelector(tau=30, cold_start_min=0, floor_exploration=0.0)

        # Seed all arms with an initial observation so select() works
        for arm_id in arm_ids:
            bandit1.update(arm_id, 0.5)

        for i in range(10):
            decision = bandit1.select(arm_ids)
            reward = 0.3 + (i % 3) * 0.3  # Varying rewards: 0.3, 0.6, 0.9, ...
            bandit1.update(decision.selected_id, reward)

        # Serialize
        json_str = bandit1.to_json()
        assert isinstance(json_str, str)
        assert len(json_str) > 0

        # Phase 2: Deserialize into a new BanditSelector
        bandit2 = BanditSelector.from_json(json_str)

        # Verify arm IDs are preserved
        # Access internal state to check -- this is an integration test
        assert set(bandit2._arms.keys()) == set(arm_ids)

        # Run 10 more cycles on the deserialized bandit
        for i in range(10):
            decision = bandit2.select(arm_ids)
            reward = 0.4 + (i % 2) * 0.4  # Varying rewards: 0.4, 0.8, ...
            bandit2.update(decision.selected_id, reward)

        # Verify total observation count across all arms = 5 (initial) + 10 + 10 = 25
        # Each arm got 1 initial update, then 10 select/updates distributed among arms,
        # then 10 more. Total exposure_count across all arms = 5 + 10 + 10 = 25
        total_observations = sum(arm.exposure_count for arm in bandit2._arms.values())
        assert total_observations == 25, (
            f"Expected 25 total observations (5 init + 10 + 10), got {total_observations}"
        )

        # Verify posterior parameters are non-default for at least some arms
        # Default is alpha=2.0, beta=1.0
        non_default_count = 0
        for arm in bandit2._arms.values():
            if arm.alpha != 2.0 or arm.beta != 1.0:
                non_default_count += 1

        assert non_default_count > 0, (
            "At least some arms should have non-default posterior parameters"
        )

        # Verify arm windows contain observations
        total_window_entries = sum(len(arm.window) for arm in bandit2._arms.values())
        assert total_window_entries > 0, "Arms should have observations in their windows"

        # Verify tau was preserved
        assert bandit2._tau == 30
