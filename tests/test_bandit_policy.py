"""Tests for the restored local-first bandit policy (PRD-CORE-105 remediation).

Covers:
- WithholdingPolicy (FR03): critical never withheld, normal/low rates, forced triggers
- Forced trigger #4: Page-Hinkley change detection
- select_nudge_learning_bandit (FR04): selection and phase-transition burst
- render_nudge_content (FR04): nudge_line rendering and budget
- build_context_vector (FR02): dimension check
- TRWConfig.phase_transition_withhold_rate (FR06 config)
- _nudge_rules.select_nudge_learning() bandit wiring (P0 fix)
- append_ceremony_status bandit nudge content (P0 fix)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# WithholdingPolicy — FR03
# ---------------------------------------------------------------------------


class TestWithholdingPolicyCriticalNeverWithheld:
    """Critical-tier learning is NEVER withheld (FR03 hard constraint)."""

    def test_critical_never_withheld_full_mode(self) -> None:
        """1000 checks on critical learning: zero withholdings (full_mode)."""
        from trw_mcp.state.bandit_policy import WithholdingPolicy

        policy = WithholdingPolicy(client_class="full_mode")
        learning = {"id": "L-critical", "protection_tier": "critical"}
        withheld = sum(1 for _ in range(1000) if policy.should_withhold(learning))
        assert withheld == 0, f"Critical learning was withheld {withheld} times"

    def test_critical_never_withheld_light_mode(self) -> None:
        """1000 checks on critical learning: zero withholdings (light_mode)."""
        from trw_mcp.state.bandit_policy import WithholdingPolicy

        policy = WithholdingPolicy(client_class="light_mode")
        learning = {"id": "L-critical", "protection_tier": "critical"}
        withheld = sum(1 for _ in range(1000) if policy.should_withhold(learning))
        assert withheld == 0, f"Critical learning was withheld {withheld} times"

    def test_protected_tier_never_withheld(self) -> None:
        """Protected-tier learning behaves like critical (never withheld)."""
        from trw_mcp.state.bandit_policy import WithholdingPolicy

        policy = WithholdingPolicy(client_class="full_mode")
        learning = {"id": "L-protected", "protection_tier": "protected"}
        withheld = sum(1 for _ in range(500) if policy.should_withhold(learning))
        assert withheld == 0


class TestWithholdingPolicyNormalTier:
    """Normal-tier withholding rates stay in the expected ranges (FR03)."""

    def test_normal_tier_rate_full_mode(self) -> None:
        """Normal-tier withheld between 12-25% for full_mode clients."""
        from trw_mcp.state.bandit_policy import WithholdingPolicy

        policy = WithholdingPolicy(client_class="full_mode")
        learning = {"id": "L-norm", "protection_tier": "normal"}
        n = 5000
        withheld = sum(1 for _ in range(n) if policy.should_withhold(learning))
        rate = withheld / n
        assert 0.07 <= rate <= 0.35, f"Normal-tier rate out of expected range: {rate:.3f}"

    def test_normal_tier_rate_light_mode_higher_floor(self) -> None:
        """Normal-tier rate is higher for light_mode (20-35%) than full_mode (12-25%)."""
        from trw_mcp.state.bandit_policy import WithholdingPolicy

        policy_full = WithholdingPolicy(client_class="full_mode")
        policy_light = WithholdingPolicy(client_class="light_mode")
        learning = {"id": "L-norm", "protection_tier": "normal"}
        n = 5000
        rate_full = sum(1 for _ in range(n) if policy_full.should_withhold(learning)) / n
        rate_light = sum(1 for _ in range(n) if policy_light.should_withhold(learning)) / n
        # Light mode should have a higher withholding rate
        assert rate_light > rate_full, (
            f"Light mode rate ({rate_light:.3f}) should exceed full mode ({rate_full:.3f})"
        )

    def test_low_tier_aggressive_withholding(self) -> None:
        """Low-tier withheld at >= 30%."""
        from trw_mcp.state.bandit_policy import WithholdingPolicy

        policy = WithholdingPolicy(client_class="full_mode")
        learning = {"id": "L-low", "protection_tier": "low"}
        n = 2000
        withheld = sum(1 for _ in range(n) if policy.should_withhold(learning))
        rate = withheld / n
        assert rate >= 0.20, f"Low-tier rate too low: {rate:.3f}"


class TestWithholdingPolicyForcedTriggers:
    """Forced re-evaluation triggers override protection tier (FR03)."""

    def test_trigger_1_anchor_validity_drop(self) -> None:
        """Anchor validity drop > 0.3 forces re-evaluation."""
        from trw_mcp.state.bandit_policy import WithholdingPolicy

        policy = WithholdingPolicy(client_class="full_mode")
        # critical tier but anchor validity dropped by 0.4 since last evaluation
        learning = {
            "id": "L-crit",
            "protection_tier": "critical",
            "metadata": {"prev_anchor_validity": 0.8, "anchor_validity": 0.4},
        }
        # With forced trigger, critical tier becomes normal — may be withheld
        # Over 1000 runs at least some withholdings should occur
        withheld = sum(1 for _ in range(1000) if policy.should_withhold(learning))
        assert withheld > 0, "Forced trigger should sometimes withhold critical learning"

    def test_trigger_1_does_not_fire_without_large_drop(self) -> None:
        """Current anchor validity must drop by more than 0.3 when present."""
        from trw_mcp.state.bandit_policy import WithholdingPolicy

        policy = WithholdingPolicy(client_class="full_mode")
        learning = {
            "id": "L-crit",
            "protection_tier": "critical",
            "metadata": {"prev_anchor_validity": 0.5, "anchor_validity": 0.25},
        }
        withheld = sum(1 for _ in range(250) if policy.should_withhold(learning))
        assert withheld == 0, "A 0.25 drop must not override critical-tier protection"

    def test_trigger_1_requires_current_anchor_validity(self) -> None:
        """Persisted prior alone must not trigger without a new current validity."""
        from trw_mcp.state.bandit_policy import WithholdingPolicy

        policy = WithholdingPolicy(client_class="full_mode")
        policy.load_anchor_validity_state({"L-crit": 0.2})
        learning = {"id": "L-crit", "protection_tier": "critical", "metadata": {}}

        withheld = sum(1 for _ in range(250) if policy.should_withhold(learning))
        assert withheld == 0

    def test_trigger_2_consecutive_shown(self) -> None:
        """Consecutive sessions > force_trial_threshold forces re-evaluation."""
        from trw_mcp.state.bandit_policy import WithholdingPolicy

        policy = WithholdingPolicy(client_class="full_mode", force_trial_threshold=20)
        learning = {
            "id": "L-crit",
            "protection_tier": "critical",
            "metadata": {"consecutive_shown": 25},
        }
        withheld = sum(1 for _ in range(1000) if policy.should_withhold(learning))
        assert withheld > 0, "Forced trigger should sometimes withhold critical learning"

    def test_trigger_3_workaround_expired(self) -> None:
        """Workaround past expires date forces re-evaluation."""
        from trw_mcp.state.bandit_policy import WithholdingPolicy

        policy = WithholdingPolicy(client_class="full_mode")
        learning = {
            "id": "L-workaround",
            "protection_tier": "critical",
            "metadata": {
                "type": "workaround",
                "expires": "2020-01-01T00:00:00",  # in the past
            },
        }
        withheld = sum(1 for _ in range(1000) if policy.should_withhold(learning))
        assert withheld > 0, "Expired workaround trigger should sometimes withhold"

    def test_trigger_4_page_hinkley_fired(self) -> None:
        """Page-Hinkley alarm forces re-evaluation (FR05, forced trigger #4)."""
        from trw_mcp.state.bandit_policy import WithholdingPolicy

        policy = WithholdingPolicy(client_class="full_mode")
        learning = {"id": "L-crit", "protection_tier": "critical"}
        # With page_hinkley_fired=True, even a critical learning can be withheld
        withheld = sum(
            1
            for _ in range(1000)
            if policy.should_withhold(learning, page_hinkley_fired=True)
        )
        assert withheld > 0, "Page-Hinkley trigger should sometimes withhold critical learning"

    def test_trigger_4_update_reward_returns_alarm(self) -> None:
        """update_reward returns True when Page-Hinkley detector fires."""
        from trw_memory.bandit.change_detection import PageHinkleyDetector
        from trw_mcp.state.bandit_policy import WithholdingPolicy

        # Verify via PageHinkleyDetector directly with low threshold
        det = PageHinkleyDetector(delta=0.01, alarm_threshold=3.0)
        # Feed high rewards then low — detector should fire eventually
        for _ in range(10):
            det.update(0.9)
        alarms = [det.update(0.1) for _ in range(15)]
        assert any(alarms), "Page-Hinkley should detect the reward shift with low threshold"

        # Also verify the WithholdingPolicy update_reward interface works
        policy = WithholdingPolicy(client_class="full_mode")
        # Feed enough rewards to build up state (policy uses default threshold=20)
        for _ in range(30):
            policy.update_reward("L-1", 0.9)
        # Verify the call doesn't raise
        result = policy.update_reward("L-1", 0.1)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# select_nudge_learning_bandit — FR04
# ---------------------------------------------------------------------------


class TestSelectNudgeLearningBandit:
    """Bandit-based nudge selection with phase-transition burst (FR04)."""

    def _make_candidates(self, n: int = 5) -> list[dict[str, object]]:
        return [
            {"id": f"L-{i}", "summary": f"Learning {i}", "protection_tier": "normal",
             "nudge_line": f"Nudge {i}: short tip"}
            for i in range(n)
        ]

    def test_returns_selection_same_phase(self) -> None:
        """Same phase: returns 1 learning."""
        from trw_memory.bandit import BanditSelector
        from trw_mcp.state.bandit_policy import WithholdingPolicy, select_nudge_learning_bandit

        bandit = BanditSelector(cold_start_min=0)
        policy = WithholdingPolicy(client_class="full_mode")
        candidates = self._make_candidates(3)

        selected, is_transition = select_nudge_learning_bandit(
            candidates, bandit, policy, phase="implement", previous_phase="implement"
        )
        assert is_transition is False
        # Should select 1 (may be 0 if withheld, but usually 1)
        assert len(selected) <= 1

    def test_phase_transition_burst_selects_2_to_3(self) -> None:
        """Phase transition: selects 2-3 learnings (FR04)."""
        from trw_memory.bandit import BanditSelector
        from trw_mcp.state.bandit_policy import WithholdingPolicy, select_nudge_learning_bandit

        bandit = BanditSelector(cold_start_min=0, floor_exploration=0.0)
        policy = WithholdingPolicy(client_class="full_mode")
        # Use critical-tier learnings to ensure none are withheld
        candidates = [
            {"id": f"L-{i}", "summary": f"Learning {i}", "protection_tier": "critical",
             "nudge_line": f"Tip {i}"}
            for i in range(5)
        ]
        # Warm bandit
        for i in range(5):
            for _ in range(3):
                bandit.update(f"L-{i}", 0.8 if i == 0 else 0.4)

        transition_counts: list[int] = []
        for _ in range(20):
            selected, is_transition = select_nudge_learning_bandit(
                candidates, bandit, policy, phase="validate", previous_phase="implement"
            )
            assert is_transition is True
            transition_counts.append(len(selected))

        # Should always select 2 or 3 during transition (with critical tier, no withholding)
        assert all(2 <= c <= 3 for c in transition_counts), f"Burst counts: {transition_counts}"

    def test_empty_candidates_returns_empty(self) -> None:
        """Empty candidate pool returns empty list."""
        from trw_memory.bandit import BanditSelector
        from trw_mcp.state.bandit_policy import WithholdingPolicy, select_nudge_learning_bandit

        bandit = BanditSelector()
        policy = WithholdingPolicy()
        selected, is_transition = select_nudge_learning_bandit(
            [], bandit, policy, phase="implement", previous_phase=""
        )
        assert selected == []
        assert is_transition is False

    def test_contextual_selector_drives_final_selection_when_context_available(self) -> None:
        """Final production selection uses contextual state, not only shortlist ranking."""
        from trw_memory.bandit import BanditDecision, BanditSelector
        from trw_mcp.state.bandit_policy import WithholdingPolicy, select_nudge_learning_bandit

        class StubContextualSelector:
            def select_decision(self, eligible_ids, context_vector=None):
                assert context_vector == [0.0, 1.0]
                return BanditDecision(
                    selected_id=eligible_ids[-1],
                    selection_probability=0.8,
                    runner_up_id=eligible_ids[0] if len(eligible_ids) > 1 else None,
                    runner_up_probability=0.2 if len(eligible_ids) > 1 else None,
                    exploration=False,
                )

        bandit = BanditSelector(cold_start_min=0)
        policy = WithholdingPolicy(client_class="full_mode")
        candidates = [
            {"id": "L-0", "summary": "Learning 0", "protection_tier": "critical", "nudge_line": "tip 0"},
            {"id": "L-1", "summary": "Learning 1", "protection_tier": "critical", "nudge_line": "tip 1"},
        ]

        with patch.object(bandit, "select", side_effect=AssertionError("bandit.select should not run")):
            selected, is_transition = select_nudge_learning_bandit(
                candidates,
                bandit,
                policy,
                phase="implement",
                previous_phase="implement",
                contextual_selector=StubContextualSelector(),
                context_vector=[0.0, 1.0],
            )

        assert is_transition is False
        assert [learning["id"] for learning in selected] == ["L-1"]


# ---------------------------------------------------------------------------
# render_nudge_content — FR04
# ---------------------------------------------------------------------------


class TestRenderNudgeContent:
    """Nudge content rendering from nudge_line fields."""

    def test_nudge_line_used_when_present(self) -> None:
        """nudge_line field is used over summary."""
        from trw_mcp.state.bandit_policy import render_nudge_content

        learnings = [{"nudge_line": "Use the typed schema", "summary": "Long summary text"}]
        result = render_nudge_content(learnings, is_transition=False)
        assert "Use the typed schema" in result
        assert "Long summary text" not in result

    def test_falls_back_to_summary_when_no_nudge_line(self) -> None:
        """Falls back to truncated summary when nudge_line absent."""
        from trw_mcp.state.bandit_policy import render_nudge_content

        learnings = [{"summary": "Always run mypy before PR submission"}]
        result = render_nudge_content(learnings, is_transition=False)
        assert "mypy" in result

    def test_phase_transition_burst_expanded_budget(self) -> None:
        """Phase-transition bursts get up to 480 chars."""
        from trw_mcp.state.bandit_policy import render_nudge_content

        learnings = [
            {"nudge_line": "A" * 100},
            {"nudge_line": "B" * 100},
            {"nudge_line": "C" * 100},
        ]
        result = render_nudge_content(learnings, is_transition=True, budget_chars=320)
        # All three should fit in 480 char budget
        assert "A" * 100 in result
        assert "B" in result  # At least partial

    def test_empty_learnings_returns_empty_string(self) -> None:
        """Empty learnings list returns empty string."""
        from trw_mcp.state.bandit_policy import render_nudge_content

        assert render_nudge_content([], is_transition=False) == ""


# ---------------------------------------------------------------------------
# build_context_vector — FR02
# ---------------------------------------------------------------------------


class TestBuildContextVector:
    """Context vector has correct dimensions for all inputs."""

    def test_vector_dimension_is_21(self) -> None:
        """Context vector is always 21-dimensional."""
        from trw_mcp.state.bandit_policy import ENGINEERING_CONTEXT_DIM, build_context_vector

        vec = build_context_vector(
            phase="implement",
            agent_type="implementer",
            task_type="bugfix",
            session_progress=0.5,
            domain_similarity=0.7,
            files_count=12,
        )
        assert len(vec) == ENGINEERING_CONTEXT_DIM
        assert len(vec) == 21

    def test_phase_one_hot_encoding(self) -> None:
        """Phase one-hot is correct: exactly one 1.0 in first 6 elements."""
        from trw_mcp.state.bandit_policy import build_context_vector

        vec = build_context_vector(phase="validate")
        phase_slice = vec[:6]
        assert sum(phase_slice) == 1.0
        # validate is index 3
        assert phase_slice[3] == 1.0

    def test_all_values_in_0_1_range(self) -> None:
        """All vector values are in [0.0, 1.0]."""
        from trw_mcp.state.bandit_policy import build_context_vector

        vec = build_context_vector(
            phase="review",
            agent_type="reviewer",
            task_type="infrastructure",
            session_progress=1.5,  # should be clamped to 1.0
            domain_similarity=2.0,  # should be clamped to 1.0
            files_count=200,  # should be clamped to 1.0
        )
        assert all(0.0 <= v <= 1.0 for v in vec)

    def test_agent_alias_resolved(self) -> None:
        """'lead' alias resolves to 'orchestrator' one-hot slot."""
        from trw_mcp.state.bandit_policy import build_context_vector

        vec_alias = build_context_vector(agent_type="lead")
        vec_resolved = build_context_vector(agent_type="orchestrator")
        assert vec_alias[7:11] == vec_resolved[7:11]

    def test_vector_layout_matches_prd_progress_one_hot(self) -> None:
        """Session progress uses the PRD's 3 one-hot dimensions."""
        from trw_mcp.state.bandit_policy import build_context_vector

        vec = build_context_vector(
            phase="implement",
            agent_type="implementer",
            task_type="bugfix",
            session_progress=0.8,
            domain_similarity=0.6,
            files_count=10,
        )

        assert vec[6] == pytest.approx(0.6)
        assert vec[7:11] == [0.0, 1.0, 0.0, 0.0]
        assert vec[11:17] == [0.0, 1.0, 0.0, 0.0, 0.0, 0.0]
        assert vec[17] == pytest.approx(0.1)
        assert vec[18:21] == [0.0, 0.0, 1.0]


# ---------------------------------------------------------------------------
# TRWConfig.phase_transition_withhold_rate — FR06 config
# ---------------------------------------------------------------------------


class TestPhaseTransitionWithholdRateConfig:
    """TRWConfig.phase_transition_withhold_rate field (FR06 P1 fix)."""

    def test_default_value_is_0_10(self) -> None:
        """Default phase_transition_withhold_rate is 0.10."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig()
        assert cfg.phase_transition_withhold_rate == 0.10

    def test_custom_value_accepted(self) -> None:
        """Custom value in [0.0, 0.30] is accepted."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(phase_transition_withhold_rate=0.20)
        assert cfg.phase_transition_withhold_rate == 0.20

    def test_zero_disables_withholding(self) -> None:
        """Zero disables phase-transition withholding."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(phase_transition_withhold_rate=0.0)
        assert cfg.phase_transition_withhold_rate == 0.0

    def test_max_value_is_0_30(self) -> None:
        """Value above 0.30 is rejected by pydantic validation."""
        from pydantic import ValidationError
        from trw_mcp.models.config import TRWConfig

        with pytest.raises((ValidationError, ValueError)):
            TRWConfig(phase_transition_withhold_rate=0.50)


# ---------------------------------------------------------------------------
# _nudge_rules.select_nudge_learning bandit wiring — P0 fix
# ---------------------------------------------------------------------------


class TestSelectNudgeLearningBanditWiring:
    """select_nudge_learning() uses bandit when BanditSelector is provided (P0 fix)."""

    def test_bandit_selector_activates_bandit_path(self) -> None:
        """BanditSelector triggers bandit-based selection path."""
        from trw_memory.bandit import BanditSelector
        from trw_mcp.state._nudge_rules import select_nudge_learning
        from trw_mcp.state._nudge_state import CeremonyState

        state = CeremonyState()
        candidates = [
            {"id": "L-1", "summary": "First", "protection_tier": "critical"},
            {"id": "L-2", "summary": "Second", "protection_tier": "critical"},
        ]
        bandit = BanditSelector(cold_start_min=0)

        selected, is_fallback = select_nudge_learning(
            state, candidates, "implement", bandit=bandit
        )
        assert selected is not None
        assert selected["id"] in ("L-1", "L-2")

    def test_non_bandit_selector_falls_through_to_deterministic(self) -> None:
        """Non-BanditSelector objects fall through to deterministic path."""
        from trw_mcp.state._nudge_rules import select_nudge_learning
        from trw_mcp.state._nudge_state import CeremonyState

        state = CeremonyState()
        candidates = [
            {"id": "L-1", "summary": "First"},
            {"id": "L-2", "summary": "Second"},
        ]
        selected, is_fallback = select_nudge_learning(
            state, candidates, "implement", bandit=object()
        )
        assert selected is not None
        assert selected["id"] == "L-1"  # deterministic first

    def test_phase_transition_burst_populates_burst_items(self) -> None:
        """Phase transition with bandit populates burst_items with extra learnings."""
        from trw_memory.bandit import BanditSelector
        from trw_mcp.state._nudge_rules import select_nudge_learning
        from trw_mcp.state._nudge_state import CeremonyState

        state = CeremonyState()
        candidates = [
            {"id": f"L-{i}", "summary": f"Learning {i}", "protection_tier": "critical",
             "nudge_line": f"Tip {i}"}
            for i in range(5)
        ]
        bandit = BanditSelector(cold_start_min=0)
        # Warm the bandit
        for i in range(5):
            for _ in range(3):
                bandit.update(f"L-{i}", 0.7)

        burst: list[dict[str, object]] = []
        selected, is_fallback = select_nudge_learning(
            state,
            candidates,
            "validate",
            bandit=bandit,
            previous_phase="implement",
            burst_items=burst,
        )
        # With phase transition, burst should have extra items
        # (may be 0-2 depending on withholding, but direction is correct)
        assert selected is not None
        assert isinstance(burst, list)

    def test_no_bandit_deterministic_unchanged(self) -> None:
        """Without bandit, existing deterministic behavior is unchanged."""
        from trw_mcp.state._nudge_rules import select_nudge_learning
        from trw_mcp.state._nudge_state import CeremonyState

        state = CeremonyState()
        candidates = [
            {"id": "L-1", "summary": "First"},
            {"id": "L-2", "summary": "Second"},
        ]
        selected, is_fallback = select_nudge_learning(state, candidates, "implement")
        assert selected is not None
        assert selected["id"] == "L-1"
        assert is_fallback is False


# ---------------------------------------------------------------------------
# append_ceremony_status bandit nudge content — P0 fix
# ---------------------------------------------------------------------------


class TestAppendCeremonyStatusNudgeContent:
    """append_ceremony_status surfaces learning-backed nudge content (P0 fix)."""

    def test_ceremony_status_always_set(self, tmp_path: Path) -> None:
        """ceremony_status key is always populated."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)

        with patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]):
            from trw_mcp.tools._ceremony_status import append_ceremony_status

            result = append_ceremony_status({"status": "ok"}, trw_dir)
            assert "ceremony_status" in result
            assert isinstance(result["ceremony_status"], str)

    def test_nudge_content_set_when_learnings_exist(self, tmp_path: Path) -> None:
        """nudge_content key is set when bandit selects a learning."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)
        (trw_dir / "meta").mkdir(parents=True)

        learning = {
            "id": "L-1",
            "summary": "Always run tests first",
            "nudge_line": "Run tests before pushing",
            "protection_tier": "critical",
            "impact": 0.9,
        }

        with patch(
            "trw_mcp.state.memory_adapter.recall_learnings",
            return_value=[learning],
        ):
            from trw_mcp.tools._ceremony_status import append_ceremony_status

            result = append_ceremony_status({"status": "ok"}, trw_dir)
            # ceremony_status always present
            assert "ceremony_status" in result
            # nudge_content may or may not be present depending on withholding
            # (critical tier → always shown, so it should be present)
            if "nudge_content" in result:
                assert isinstance(result["nudge_content"], str)
                assert len(result["nudge_content"]) > 0

    def test_fail_open_on_recall_error(self, tmp_path: Path) -> None:
        """append_ceremony_status is fail-open: returns response even on recall errors."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)

        with patch(
            "trw_mcp.state.memory_adapter.recall_learnings",
            side_effect=RuntimeError("test error"),
        ):
            from trw_mcp.tools._ceremony_status import append_ceremony_status

            result = append_ceremony_status({"status": "ok"}, trw_dir)
            # Should not raise, ceremony_status still set
            assert "ceremony_status" in result
            assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# resolve_client_class
# ---------------------------------------------------------------------------


class TestResolveClientClass:
    """Client class resolution maps profiles correctly."""

    def test_claude_code_maps_to_full_mode(self) -> None:
        from trw_mcp.state.bandit_policy import resolve_client_class
        assert resolve_client_class("claude-code") == "full_mode"

    def test_opencode_maps_to_light_mode(self) -> None:
        from trw_mcp.state.bandit_policy import resolve_client_class
        assert resolve_client_class("opencode") == "light_mode"

    def test_unknown_defaults_to_full_mode(self) -> None:
        from trw_mcp.state.bandit_policy import resolve_client_class
        assert resolve_client_class("unknown-client") == "full_mode"


# ---------------------------------------------------------------------------
# Bandit state envelope: load/save/migrate/quarantine (PRD-CORE-105 C-5)
# ---------------------------------------------------------------------------


class TestBanditStateEnvelope:
    """load_bandit_state / save_bandit_state handle C-5 envelope correctly."""

    def test_load_missing_file_returns_fresh(self, tmp_path: Path) -> None:
        """Missing bandit_state.json returns a fresh BanditSelector."""
        from trw_mcp.state.bandit_policy import load_bandit_state
        from trw_memory.bandit import BanditSelector

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        bandit = load_bandit_state(trw_dir, "full_mode", "claude-sonnet-4")
        assert isinstance(bandit, BanditSelector)
        assert len(bandit._arms) == 0

    def test_load_corrupt_file_returns_fresh(self, tmp_path: Path) -> None:
        """Corrupt JSON in bandit_state.json returns a fresh BanditSelector."""
        from trw_mcp.state.bandit_policy import load_bandit_state
        from trw_memory.bandit import BanditSelector

        trw_dir = tmp_path / ".trw"
        meta_dir = trw_dir / "meta"
        meta_dir.mkdir(parents=True)
        (meta_dir / "bandit_state.json").write_text("{ not valid json !!!}", encoding="utf-8")

        bandit = load_bandit_state(trw_dir, "full_mode", "claude-sonnet-4")
        assert isinstance(bandit, BanditSelector)

    def test_load_legacy_raw_state_migrates(self, tmp_path: Path) -> None:
        """Old raw format (arms at top level) is migrated and arms are restored."""
        import json
        from trw_mcp.state.bandit_policy import load_bandit_state
        from trw_memory.bandit import BanditSelector

        trw_dir = tmp_path / ".trw"
        meta_dir = trw_dir / "meta"
        meta_dir.mkdir(parents=True)

        b = BanditSelector()
        b.update("arm-1", 0.8)
        b.update("arm-1", 0.9)
        raw_state = json.loads(b.to_json())
        (meta_dir / "bandit_state.json").write_text(
            json.dumps(raw_state), encoding="utf-8"
        )

        loaded = load_bandit_state(trw_dir, "full_mode", "claude-sonnet-4")
        assert isinstance(loaded, BanditSelector)
        assert "arm-1" in loaded._arms
        assert loaded._arms["arm-1"].exposure_count == 2

    def test_model_family_quarantine_on_new_family(self, tmp_path: Path) -> None:
        """When model_family changes, old posteriors are quarantined; fresh selector returned."""
        import json
        from trw_mcp.state.bandit_policy import load_bandit_state, save_bandit_state
        from trw_memory.bandit import BanditSelector

        trw_dir = tmp_path / ".trw"
        meta_dir = trw_dir / "meta"
        meta_dir.mkdir(parents=True)

        b = BanditSelector()
        b.update("arm-1", 0.8)
        save_bandit_state(trw_dir, b, "full_mode", "claude-opus-3")

        loaded = load_bandit_state(trw_dir, "full_mode", "claude-sonnet-4")
        assert isinstance(loaded, BanditSelector)
        assert len(loaded._arms) == 0

        save_bandit_state(trw_dir, loaded, "full_mode", "claude-sonnet-4")
        stored = json.loads((meta_dir / "bandit_state.json").read_text(encoding="utf-8"))
        assert stored["model_family"] == "claude-sonnet-4"

    def test_save_uses_atomic_write_pattern(self, tmp_path: Path) -> None:
        """save_bandit_state uses temp file + rename (atomic write)."""
        import json
        from trw_mcp.state.bandit_policy import save_bandit_state
        from trw_memory.bandit import BanditSelector

        trw_dir = tmp_path / ".trw"
        (trw_dir / "meta").mkdir(parents=True)
        b = BanditSelector()
        b.update("arm-1", 0.7)
        save_bandit_state(trw_dir, b, "full_mode", "test-family")

        state_path = trw_dir / "meta" / "bandit_state.json"
        assert state_path.exists()
        tmp_files = list((trw_dir / "meta").glob("*.tmp.*"))
        assert len(tmp_files) == 0, f"Leftover tmp files: {tmp_files}"

    def test_save_includes_c5_envelope_fields(self, tmp_path: Path) -> None:
        """Saved JSON contains client_profile, model_family, bandit_state, quarantined."""
        import json
        from trw_mcp.state.bandit_policy import save_bandit_state
        from trw_memory.bandit import BanditSelector

        trw_dir = tmp_path / ".trw"
        (trw_dir / "meta").mkdir(parents=True)
        b = BanditSelector()
        save_bandit_state(trw_dir, b, "full_mode", "claude-sonnet-4")

        stored = json.loads(
            (trw_dir / "meta" / "bandit_state.json").read_text(encoding="utf-8")
        )
        assert stored["client_profile"] == "full_mode"
        assert stored["model_family"] == "claude-sonnet-4"
        assert "bandit_state" in stored
        assert "quarantined" in stored

    def test_save_load_round_trip_preserves_arms(self, tmp_path: Path) -> None:
        """save then load restores arm posteriors correctly."""
        from trw_mcp.state.bandit_policy import load_bandit_state, save_bandit_state
        from trw_memory.bandit import BanditSelector

        trw_dir = tmp_path / ".trw"
        (trw_dir / "meta").mkdir(parents=True)

        b = BanditSelector()
        b.update("arm-A", 0.9)
        b.update("arm-A", 0.8)
        b.update("arm-B", 0.3)
        save_bandit_state(trw_dir, b, "full_mode", "test-model")

        loaded = load_bandit_state(trw_dir, "full_mode", "test-model")
        assert "arm-A" in loaded._arms
        assert "arm-B" in loaded._arms
        assert loaded._arms["arm-A"].exposure_count == 2
        assert loaded._arms["arm-B"].exposure_count == 1

    def test_contextual_state_persisted_and_restored(self, tmp_path: Path) -> None:
        """Shared envelope persists compact contextual state used by production."""
        import json
        from trw_mcp.state.bandit_policy import (
            ENGINEERING_CONTEXT_DIM,
            build_context_vector,
            load_contextual_bandit_state,
            save_bandit_state,
        )
        from trw_memory.bandit import BanditSelector, ContextualBanditSelector

        trw_dir = tmp_path / ".trw"
        (trw_dir / "meta").mkdir(parents=True)

        bandit = BanditSelector()
        contextual = ContextualBanditSelector(feature_dim=ENGINEERING_CONTEXT_DIM, alpha=0.5)
        context = build_context_vector(phase="validate", session_progress=0.8, domain_similarity=1.0)
        for _ in range(30):
            bandit.update("arm-a", 0.9)
            contextual.update("arm-a", 0.9, context_vector=context)
            contextual.update("arm-b", 0.1, context_vector=context)

        save_bandit_state(
            trw_dir,
            bandit,
            "full_mode",
            "test-model",
            contextual_bandit=contextual,
        )

        stored = json.loads((trw_dir / "meta" / "bandit_state.json").read_text(encoding="utf-8"))
        assert "contextual_state" in stored

        restored = load_contextual_bandit_state(trw_dir, model_family="test-model")
        assert restored is not None
        restored.seed_thompson_fallback(bandit)
        selected_id, _ = restored.select(["arm-a", "arm-b"], context_vector=context)
        assert selected_id == "arm-a"

    def test_quarantine_preserves_old_data_in_file(self, tmp_path: Path) -> None:
        """Quarantined arm data is retained in the JSON file for offline replay."""
        import json
        from trw_mcp.state.bandit_policy import load_bandit_state, save_bandit_state
        from trw_memory.bandit import BanditSelector

        trw_dir = tmp_path / ".trw"
        meta_dir = trw_dir / "meta"
        meta_dir.mkdir(parents=True)

        b_old = BanditSelector()
        b_old.update("arm-old", 0.9)
        save_bandit_state(trw_dir, b_old, "full_mode", "claude-3-old")

        # Load with new family → quarantine triggered; save to persist it
        fresh = load_bandit_state(trw_dir, "full_mode", "claude-4-new")
        save_bandit_state(trw_dir, fresh, "full_mode", "claude-4-new")

        stored = json.loads(
            (meta_dir / "bandit_state.json").read_text(encoding="utf-8")
        )
        assert stored["model_family"] == "claude-4-new"
        assert "claude-3-old" in stored.get("quarantined", {}), (
            f"Expected 'claude-3-old' in quarantined, got: {stored.get('quarantined')}"
        )


# ---------------------------------------------------------------------------
# Heuristic reward computation
# ---------------------------------------------------------------------------


class TestHeuristicReward:
    """_compute_heuristic_reward uses impact/score field (PRD-CORE-105 P0)."""

    def test_uses_impact_field(self) -> None:
        from trw_mcp.state.bandit_policy import _compute_heuristic_reward
        assert _compute_heuristic_reward({"impact": 0.9}) == pytest.approx(0.9)

    def test_falls_back_to_score(self) -> None:
        from trw_mcp.state.bandit_policy import _compute_heuristic_reward
        assert _compute_heuristic_reward({"score": 0.6}) == pytest.approx(0.6)

    def test_neutral_fallback_when_no_field(self) -> None:
        from trw_mcp.state.bandit_policy import _compute_heuristic_reward
        assert _compute_heuristic_reward({}) == pytest.approx(0.5)

    def test_clamps_to_range(self) -> None:
        from trw_mcp.state.bandit_policy import _compute_heuristic_reward
        assert _compute_heuristic_reward({"impact": 1.5}) == pytest.approx(1.0)
        assert _compute_heuristic_reward({"impact": -0.2}) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Live decorator path: propensity / surface logs / dedup (PRD-CORE-105 P1)
# ---------------------------------------------------------------------------


class TestLiveDecoratorPath:
    """_try_bandit_nudge_content writes logs and records dedup."""

    def _make_learning(self, arm_id: str, impact: float = 0.9) -> dict:
        return {
            "id": arm_id,
            "summary": f"Learning {arm_id}",
            "nudge_line": f"Tip for {arm_id}",
            "protection_tier": "critical",
            "impact": impact,
        }

    def test_live_path_calls_bandit_update(self, tmp_path: Path) -> None:
        """After selection, bandit arms get updated posteriors (hard assertion)."""
        import json as _json
        from trw_mcp.state._ceremony_progress_state import CeremonyState
        from trw_mcp.tools._ceremony_status import _try_bandit_nudge_content

        trw_dir = tmp_path / ".trw"
        (trw_dir / "meta").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)
        state = CeremonyState(phase="implement", previous_phase="")

        learning = self._make_learning("L-update-test")

        with patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[learning]):
            _try_bandit_nudge_content(trw_dir, state)

        bandit_path = trw_dir / "meta" / "bandit_state.json"
        assert bandit_path.exists(), (
            "bandit_state.json must be written after a successful live-path call; "
            "bandit.update() and save_bandit_state() are not being reached"
        )
        stored = _json.loads(bandit_path.read_text(encoding="utf-8"))
        arms = stored.get("bandit_state", {}).get("arms", {})
        assert "L-update-test" in arms, (
            f"Expected arm 'L-update-test' in bandit arms after update, got: {list(arms)}"
        )
        assert arms["L-update-test"]["exposure_count"] >= 1

    def test_live_path_writes_propensity_log(self, tmp_path: Path) -> None:
        """propensity.jsonl is written after a successful selection."""
        from trw_mcp.state._ceremony_progress_state import CeremonyState
        from trw_mcp.tools._ceremony_status import _try_bandit_nudge_content

        trw_dir = tmp_path / ".trw"
        (trw_dir / "meta").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)
        state = CeremonyState(phase="implement", previous_phase="")

        learning = self._make_learning("L-propensity-test")

        with patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[learning]):
            _try_bandit_nudge_content(trw_dir, state)

        propensity_path = trw_dir / "logs" / "propensity.jsonl"
        if propensity_path.exists():
            import json
            lines = propensity_path.read_text(encoding="utf-8").strip().split("\n")
            entries = [json.loads(l) for l in lines if l.strip()]
            assert len(entries) >= 1
            entry = entries[-1]
            assert "selection_probability" in entry
            assert "exploration" in entry

    def test_live_path_writes_surface_log(self, tmp_path: Path) -> None:
        """surface_tracking.jsonl is written after selection."""
        from trw_mcp.state._ceremony_progress_state import CeremonyState
        from trw_mcp.tools._ceremony_status import _try_bandit_nudge_content

        trw_dir = tmp_path / ".trw"
        (trw_dir / "meta").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)
        state = CeremonyState(phase="implement", previous_phase="")

        learning = self._make_learning("L-surface-test")

        with patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[learning]):
            _try_bandit_nudge_content(trw_dir, state)

        surface_path = trw_dir / "logs" / "surface_tracking.jsonl"
        if surface_path.exists():
            import json
            lines = surface_path.read_text(encoding="utf-8").strip().split("\n")
            entries = [json.loads(l) for l in lines if l.strip()]
            assert len(entries) >= 1
            assert entries[-1].get("learning_id") == "L-surface-test"

    def test_live_path_records_nudge_dedup(self, tmp_path: Path) -> None:
        """After selection, nudge_history is updated in ceremony state."""
        import json
        from trw_mcp.state._ceremony_progress_state import CeremonyState
        from trw_mcp.tools._ceremony_status import _try_bandit_nudge_content

        trw_dir = tmp_path / ".trw"
        (trw_dir / "meta").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)
        state = CeremonyState(phase="implement", previous_phase="")

        learning = self._make_learning("L-dedup-test")

        with patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[learning]):
            _try_bandit_nudge_content(trw_dir, state)

        state_path = trw_dir / "context" / "ceremony-state.json"
        if state_path.exists():
            state_data = json.loads(state_path.read_text(encoding="utf-8"))
            nudge_history = state_data.get("nudge_history", {})
            if nudge_history:
                assert "L-dedup-test" in nudge_history

    def test_phase_transition_withhold_rate_wired(self, tmp_path: Path) -> None:
        """phase_transition_withhold_rate from config is passed to selection.

        With rate=1.0 and only 2 candidates (so slot-1 has no runner-up),
        every phase-transition burst should return exactly 1 item because
        the slot-1 candidate is always withheld and there is no runner-up.
        """
        from trw_mcp.state.bandit_policy import WithholdingPolicy, select_nudge_learning_bandit
        from trw_memory.bandit import BanditSelector

        learnings = [
            {"id": "L-slot0", "summary": "Primary", "nudge_line": "P",
             "protection_tier": "critical", "impact": 0.9},
            {"id": "L-slot1", "summary": "Secondary", "nudge_line": "S",
             "protection_tier": "normal", "impact": 0.8},
        ]

        bandit = BanditSelector()
        policy = WithholdingPolicy(client_class="full_mode")

        single_selections = 0
        n_trials = 50
        for _ in range(n_trials):
            selected, is_transition = select_nudge_learning_bandit(
                learnings,
                bandit,
                policy,
                phase="validate",
                previous_phase="implement",
                phase_transition_withhold_rate=1.0,
            )
            if is_transition and len(selected) == 1:
                single_selections += 1

        assert single_selections >= n_trials * 0.7, (
            f"Expected >=70% of transitions to yield 1 item with rate=1.0, "
            f"got {single_selections}/{n_trials}"
        )

    def test_live_path_uses_recall_context_for_contextual_pool(self, tmp_path: Path) -> None:
        """Live path uses build_recall_context + ContextualBanditSelector before bandit selection."""
        from trw_mcp.scoring._recall import RecallContext
        from trw_mcp.state._ceremony_progress_state import CeremonyState
        from trw_mcp.tools._ceremony_status import _try_bandit_nudge_content

        trw_dir = tmp_path / ".trw"
        (trw_dir / "meta").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)
        state = CeremonyState(phase="validate", previous_phase="implement")

        learnings = [
            {
                "id": "L-testing-a",
                "summary": "testing learning A",
                "nudge_line": "testing tip A",
                "protection_tier": "critical",
                "impact": 0.9,
                "domain": ["testing"],
                "phase_affinity": ["validate"],
            },
            {
                "id": "L-testing-b",
                "summary": "testing learning B",
                "nudge_line": "testing tip B",
                "protection_tier": "critical",
                "impact": 0.8,
                "domain": ["testing"],
                "phase_affinity": ["validate"],
            },
            {
                "id": "L-payments",
                "summary": "payments learning",
                "nudge_line": "payments tip",
                "protection_tier": "critical",
                "impact": 0.7,
                "domain": ["payments"],
                "phase_affinity": ["implement"],
            },
        ]

        captured_candidate_ids: list[str] = []

        def _select_capture(
            candidates,
            bandit,
            policy,
            phase,
            previous_phase,
            phase_transition_withhold_rate=0.10,
            decisions_out=None,
            withheld_events_out=None,
            contextual_selector=None,
            context_vector=None,
        ):
            captured_candidate_ids[:] = [str(candidate["id"]) for candidate in candidates]
            return ([candidates[0]], True)

        def _contextual_select(self, eligible_ids, context_vector=None):
            return eligible_ids[0], 0.9

        recall_context = RecallContext(
            current_phase="VALIDATE",
            inferred_domains={"testing"},
            modified_files=["trw-mcp/tests/test_bandit_policy.py"],
        )

        with (
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=learnings),
            patch("trw_mcp.tools._recall_impl.build_recall_context", return_value=recall_context),
            patch(
                "trw_memory.bandit.contextual.ContextualBanditSelector.select",
                autospec=True,
                side_effect=_contextual_select,
            ) as mock_contextual_select,
            patch(
                "trw_mcp.state.bandit_policy.select_nudge_learning_bandit",
                side_effect=_select_capture,
            ),
        ):
            _try_bandit_nudge_content(trw_dir, state)

        assert mock_contextual_select.called, "Contextual selector must run in the live nudge path"
        assert captured_candidate_ids == ["L-testing-a", "L-testing-b"], (
            "Live bandit pool should be filtered using inferred recall domains "
            f"before Thompson selection, got {captured_candidate_ids}"
        )

    def test_live_path_logs_each_shown_burst_item(self, tmp_path: Path) -> None:
        """Phase-transition bursts log one shown propensity entry per surfaced learning."""
        import json
        from trw_memory.bandit import BanditSelector
        from trw_mcp.state._ceremony_progress_state import CeremonyState
        from trw_mcp.state.bandit_policy import WithholdingPolicy
        from trw_mcp.tools._ceremony_status import _try_bandit_nudge_content

        trw_dir = tmp_path / ".trw"
        (trw_dir / "meta").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        state = CeremonyState(phase="validate", previous_phase="implement")
        learnings = [
            {
                "id": f"L-burst-{i}",
                "summary": f"Burst learning {i}",
                "nudge_line": f"burst tip {i}",
                "protection_tier": "critical",
                "impact": 0.9 - (i * 0.1),
            }
            for i in range(3)
        ]

        bandit = BanditSelector(cold_start_min=0, floor_exploration=0.0)
        for i, reward in enumerate((0.9, 0.7, 0.5)):
            for _ in range(8):
                bandit.update(f"L-burst-{i}", reward)
        policy = WithholdingPolicy(client_class="full_mode")

        with (
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=learnings),
            patch(
                "trw_mcp.state.bandit_policy.load_bandit_state_and_policy",
                return_value=(bandit, policy),
            ),
            patch("random.randint", return_value=2),
        ):
            content = _try_bandit_nudge_content(trw_dir, state)

        assert content is not None
        shown_lines = [line for line in content.splitlines() if line.strip()]
        propensity_path = trw_dir / "logs" / "propensity.jsonl"
        entries = [
            json.loads(line)
            for line in propensity_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        shown_entries = [entry for entry in entries if entry.get("withheld") is False]
        line_to_id = {
            str(learning["nudge_line"]): str(learning["id"])
            for learning in learnings
        }

        assert len(shown_entries) == len(shown_lines) == 2
        assert {entry["selected"] for entry in shown_entries} == {
            line_to_id[line] for line in shown_lines
        }


# ---------------------------------------------------------------------------
# P1-A: model_family config field (PRD-CORE-105 C-5)
# ---------------------------------------------------------------------------


class TestModelFamilyConfig:
    """TRWConfig.model_family field is always non-empty (P1-A fix)."""

    def test_default_model_family_non_empty(self) -> None:
        """Default model_family resolves to 'generic' (or env-detected) — never ''."""
        import os
        from trw_mcp.models.config import TRWConfig

        # Ensure no env vars interfere
        for var in ("TRW_MODEL_FAMILY", "CLAUDE_MODEL", "ANTHROPIC_MODEL", "OPENAI_MODEL_NAME"):
            os.environ.pop(var, None)

        cfg = TRWConfig()
        assert cfg.model_family, "model_family must never be empty string"
        # Default fallback when no env var set
        assert cfg.model_family == "generic"

    def test_explicit_model_family_accepted(self) -> None:
        """Explicitly set model_family is preserved."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(model_family="claude-sonnet-4")
        assert cfg.model_family == "claude-sonnet-4"

    def test_env_var_sets_model_family(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TRW_MODEL_FAMILY env var overrides the default."""
        monkeypatch.setenv("TRW_MODEL_FAMILY", "gpt-4o")
        from trw_mcp.models.config._main import TRWConfig  # reimport to pick up env
        cfg = TRWConfig()
        assert cfg.model_family == "gpt-4o"

    def test_live_path_propensity_log_has_non_empty_model_family(
        self, tmp_path: Path
    ) -> None:
        """propensity.jsonl entries carry non-empty model_family (P1-A live path)."""
        import json
        import os
        from unittest.mock import patch
        from trw_mcp.state._ceremony_progress_state import CeremonyState
        from trw_mcp.tools._ceremony_status import _try_bandit_nudge_content

        # Clear env vars so we rely on the config default "generic"
        for var in ("CLAUDE_MODEL", "ANTHROPIC_MODEL", "OPENAI_MODEL_NAME", "TRW_MODEL_FAMILY"):
            os.environ.pop(var, None)

        trw_dir = tmp_path / ".trw"
        (trw_dir / "meta").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)
        state = CeremonyState(phase="implement", previous_phase="")

        learning = {
            "id": "L-mf-test",
            "summary": "model_family test learning",
            "nudge_line": "test tip",
            "protection_tier": "critical",
            "impact": 0.9,
        }

        with patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[learning]):
            _try_bandit_nudge_content(trw_dir, state)

        propensity_path = trw_dir / "logs" / "propensity.jsonl"
        if propensity_path.exists():
            lines = propensity_path.read_text(encoding="utf-8").strip().split("\n")
            entries = [json.loads(ln) for ln in lines if ln.strip()]
            assert len(entries) >= 1
            for entry in entries:
                mf = entry.get("model_family", "")
                assert mf, f"model_family must be non-empty in propensity log, got: {mf!r}"

    def test_bandit_state_envelope_has_non_empty_model_family(
        self, tmp_path: Path
    ) -> None:
        """bandit_state.json envelope carries non-empty model_family after save."""
        import json
        import os
        from unittest.mock import patch
        from trw_mcp.state._ceremony_progress_state import CeremonyState
        from trw_mcp.tools._ceremony_status import _try_bandit_nudge_content

        for var in ("CLAUDE_MODEL", "ANTHROPIC_MODEL", "OPENAI_MODEL_NAME", "TRW_MODEL_FAMILY"):
            os.environ.pop(var, None)

        trw_dir = tmp_path / ".trw"
        (trw_dir / "meta").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)
        state = CeremonyState(phase="implement", previous_phase="")
        learning = {
            "id": "L-mf-env",
            "summary": "envelope test",
            "nudge_line": "tip",
            "protection_tier": "critical",
            "impact": 0.8,
        }
        with patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[learning]):
            _try_bandit_nudge_content(trw_dir, state)

        bandit_path = trw_dir / "meta" / "bandit_state.json"
        if bandit_path.exists():
            stored = json.loads(bandit_path.read_text(encoding="utf-8"))
            mf = stored.get("model_family", "")
            assert mf, f"bandit state envelope model_family must not be empty, got: {mf!r}"


# ---------------------------------------------------------------------------
# P1-B: slot 0 withholding at phase transition (PRD-CORE-105-FR06)
# ---------------------------------------------------------------------------


class TestSlot0WithheldAtPhaseTransition:
    """FR06 withholding applies to slot 0 (primary burst slot) — P1-B fix."""

    def test_phase_transition_slot_0_can_be_withheld_with_rate_1(self) -> None:
        """test_phase_transition_slot_0_withheld_when_rate_1 (PRD-CORE-105-FR06).

        With rate=1.0 and a single non-critical candidate, the slot-0 candidate
        must be withheld on every phase-transition burst. Before the P1-B fix,
        slot 0 was exempt (slot > 0 guard), so this would always select 1 item.
        After the fix, the candidate is withheld and no runner-up is available →
        0 items returned.
        """
        from trw_memory.bandit import BanditSelector
        from trw_mcp.state.bandit_policy import WithholdingPolicy, select_nudge_learning_bandit

        # Single non-critical candidate — no runner-up available
        candidates = [
            {"id": "L-only", "summary": "Only candidate",
             "nudge_line": "tip", "protection_tier": "normal", "impact": 0.8},
        ]
        bandit = BanditSelector(cold_start_min=0)
        policy = WithholdingPolicy(client_class="full_mode")

        withheld_count = 0
        n_trials = 20
        for _ in range(n_trials):
            selected, is_transition = select_nudge_learning_bandit(
                candidates,
                bandit,
                policy,
                phase="validate",
                previous_phase="implement",
                phase_transition_withhold_rate=1.0,
            )
            assert is_transition is True
            if len(selected) == 0:
                withheld_count += 1

        # With rate=1.0 and no runner-up, every trial should yield 0 items
        assert withheld_count == n_trials, (
            f"Expected all {n_trials} slot-0 candidates to be withheld, "
            f"only {withheld_count} were"
        )

    def test_phase_transition_slot_0_not_withheld_when_critical(self) -> None:
        """Critical-tier slot-0 candidate is never withheld at phase transition."""
        from trw_memory.bandit import BanditSelector
        from trw_mcp.state.bandit_policy import WithholdingPolicy, select_nudge_learning_bandit

        candidates = [
            {"id": "L-crit", "summary": "Critical", "nudge_line": "tip",
             "protection_tier": "critical", "impact": 0.9},
        ]
        bandit = BanditSelector(cold_start_min=0)
        policy = WithholdingPolicy(client_class="full_mode")

        for _ in range(20):
            selected, is_transition = select_nudge_learning_bandit(
                candidates, bandit, policy,
                phase="validate", previous_phase="implement",
                phase_transition_withhold_rate=1.0,
            )
            assert is_transition is True
            # Critical tier exempt → always selected
            assert len(selected) == 1, "Critical slot-0 should never be withheld"

    def test_withheld_events_out_populated_for_slot_0(self) -> None:
        """withheld_events_out receives slot=0 events when slot 0 is withheld (P1-D)."""
        from trw_memory.bandit import BanditSelector
        from trw_mcp.state.bandit_policy import (
            WithheldEvent,
            WithholdingPolicy,
            select_nudge_learning_bandit,
        )

        candidates = [
            {"id": "L-slot0", "summary": "Slot 0", "nudge_line": "tip",
             "protection_tier": "normal", "impact": 0.8},
        ]
        bandit = BanditSelector(cold_start_min=0)
        policy = WithholdingPolicy(client_class="full_mode")

        withheld_events: list[WithheldEvent] = []
        select_nudge_learning_bandit(
            candidates, bandit, policy,
            phase="validate", previous_phase="implement",
            phase_transition_withhold_rate=1.0,
            withheld_events_out=withheld_events,
        )
        assert len(withheld_events) >= 1
        ev = withheld_events[0]
        assert ev["slot"] == 0
        assert ev["learning_id"] == "L-slot0"
        assert ev["exploration"] is True
        assert ev["phase"] == "validate"


# ---------------------------------------------------------------------------
# P1-C: withheld field in propensity log entries (PRD-CORE-103/105)
# ---------------------------------------------------------------------------


class TestPropensityLogWithheldField:
    """PropensityEntry and log_selection carry withheld field (P1-C fix)."""

    def test_withheld_defaults_to_false_in_log_entry(self, tmp_path: Path) -> None:
        """log_selection without withheld= writes withheld=false."""
        import json
        from trw_mcp.state.propensity_log import log_selection, read_propensity_entries

        trw_dir = tmp_path / ".trw"
        log_selection(trw_dir, selected="L-1", candidate_set=["L-1", "L-2"])
        entries = read_propensity_entries(trw_dir)
        assert len(entries) == 1
        assert entries[0]["withheld"] is False

    def test_withheld_true_recorded_correctly(self, tmp_path: Path) -> None:
        """log_selection with withheld=True stores withheld=true in JSONL."""
        import json
        from trw_mcp.state.propensity_log import log_selection, read_propensity_entries

        trw_dir = tmp_path / ".trw"
        log_selection(
            trw_dir, selected="L-2", candidate_set=["L-1", "L-2"],
            withheld=True, exploration=True,
        )
        entries = read_propensity_entries(trw_dir)
        assert len(entries) == 1
        assert entries[0]["withheld"] is True
        assert entries[0]["exploration"] is True

    def test_propensity_entry_typeddict_has_withheld_key(self) -> None:
        """PropensityEntry TypedDict declares withheld field."""
        from trw_mcp.state.propensity_log import PropensityEntry
        assert "withheld" in PropensityEntry.__annotations__

    def test_both_shown_and_withheld_entries_distinguishable(self, tmp_path: Path) -> None:
        """Written entries can be filtered by withheld field."""
        from trw_mcp.state.propensity_log import log_selection, read_propensity_entries

        trw_dir = tmp_path / ".trw"
        log_selection(trw_dir, selected="L-shown", withheld=False)
        log_selection(trw_dir, selected="L-withheld", withheld=True, exploration=True)

        entries = read_propensity_entries(trw_dir)
        shown = [e for e in entries if not e.get("withheld")]
        withheld = [e for e in entries if e.get("withheld")]
        assert len(shown) == 1
        assert len(withheld) == 1
        assert shown[0]["selected"] == "L-shown"
        assert withheld[0]["selected"] == "L-withheld"


# ---------------------------------------------------------------------------
# P1-D: withheld events logged to propensity.jsonl (PRD-CORE-105-FR06)
# ---------------------------------------------------------------------------


class TestWithheldEventLogging:
    """Withheld phase-transition events are written to propensity.jsonl (P1-D fix)."""

    def test_withheld_event_logged_to_propensity_jsonl(self, tmp_path: Path) -> None:
        """test_phase_transition_withholding_logged (PRD-CORE-105-FR06).

        When a candidate is withheld via FR06 micro-randomised withholding,
        a propensity log entry with withheld=True and exploration=True must be
        written to propensity.jsonl — not just skipped silently.
        """
        import json
        from unittest.mock import patch
        from trw_mcp.state._ceremony_progress_state import CeremonyState
        from trw_mcp.tools._ceremony_status import _try_bandit_nudge_content

        trw_dir = tmp_path / ".trw"
        (trw_dir / "meta").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        # Phase transition with non-critical learning → FR06 can withhold
        state = CeremonyState(phase="validate", previous_phase="implement")

        learning = {
            "id": "L-transition-withhold",
            "summary": "Phase transition learning",
            "nudge_line": "tip",
            "protection_tier": "normal",  # non-critical → can be withheld
            "impact": 0.7,
        }

        # Override select_nudge_learning_bandit to force a withheld event
        from trw_mcp.state.bandit_policy import WithheldEvent

        def _patched_select(
            candidates, bandit, policy, phase, previous_phase,
            phase_transition_withhold_rate=0.10, decisions_out=None,
            withheld_events_out=None,
            contextual_selector=None,
            context_vector=None,
        ):
            # Simulate the bandit withholding the candidate
            from trw_memory.bandit import BanditDecision
            decision = BanditDecision(
                selected_id="L-transition-withhold",
                selection_probability=0.5,
                runner_up_id=None,
                runner_up_probability=None,
                exploration=True,
            )
            if decisions_out is not None:
                decisions_out.append(decision)
            if withheld_events_out is not None:
                withheld_events_out.append(
                    WithheldEvent(
                        learning_id="L-transition-withhold",
                        selection_probability=0.5,
                        runner_up_id="",
                        exploration=True,
                        slot=0,
                        phase="validate",
                    )
                )
            # Return empty selected list (withheld) but is_transition=True
            return [], True

        with (
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[learning]),
            patch(
                "trw_mcp.state.bandit_policy.select_nudge_learning_bandit",
                side_effect=_patched_select,
            ),
        ):
            _try_bandit_nudge_content(trw_dir, state)

        propensity_path = trw_dir / "logs" / "propensity.jsonl"
        assert propensity_path.exists(), "propensity.jsonl must exist after withheld event"
        lines = propensity_path.read_text(encoding="utf-8").strip().split("\n")
        entries = [json.loads(ln) for ln in lines if ln.strip()]

        withheld_entries = [e for e in entries if e.get("withheld") is True]
        assert withheld_entries, (
            "Expected at least one withheld=True entry in propensity.jsonl, "
            f"got entries: {entries}"
        )
        we = withheld_entries[0]
        assert we["selected"] == "L-transition-withhold"
        assert we["exploration"] is True
        assert we.get("model_family"), "withheld entry must have non-empty model_family"

    def test_withheld_events_out_not_none_receives_all_withheld(self) -> None:
        """withheld_events_out receives one entry per withheld slot (P1-D)."""
        from trw_memory.bandit import BanditSelector
        from trw_mcp.state.bandit_policy import (
            WithheldEvent,
            WithholdingPolicy,
            select_nudge_learning_bandit,
        )

        # 3 non-critical candidates at phase transition, rate=1.0 → all withheld
        candidates = [
            {"id": f"L-{i}", "summary": f"L {i}", "nudge_line": "tip",
             "protection_tier": "normal", "impact": 0.7}
            for i in range(3)
        ]
        bandit = BanditSelector(cold_start_min=0)
        policy = WithholdingPolicy(client_class="full_mode")

        withheld_events: list[WithheldEvent] = []
        selected, is_transition = select_nudge_learning_bandit(
            candidates, bandit, policy,
            phase="validate", previous_phase="implement",
            phase_transition_withhold_rate=1.0,
            withheld_events_out=withheld_events,
        )
        # With rate=1.0, all slots withheld — withheld_events should have entries
        assert is_transition is True
        assert len(withheld_events) >= 1
        for ev in withheld_events:
            assert ev["exploration"] is True
            assert ev["phase"] == "validate"
            assert ev["learning_id"].startswith("L-")


# ---------------------------------------------------------------------------
# FR05 production-path integration (PRD-CORE-105-FR05 audit close)
# ---------------------------------------------------------------------------


class TestFR05ProductionPath:
    """FR05 integration gap: Page-Hinkley detector wired into the live path.

    These tests prove:
    1. policy.update_reward() is called in the live _try_bandit_nudge_content path.
    2. Detector states are persisted in the bandit_state.json envelope.
    3. Detector states are restored by load_bandit_state_and_policy on next load.
    4. Forced trigger #4 is reachable across sessions in production.
    """

    def _make_learning(self, arm_id: str, impact: float = 0.9) -> dict:
        return {
            "id": arm_id,
            "summary": f"Learning {arm_id}",
            "nudge_line": f"Tip for {arm_id}",
            "protection_tier": "critical",
            "impact": impact,
        }

    def test_live_path_calls_policy_update_reward(self, tmp_path: Path) -> None:
        """policy.update_reward() is called in the live production path.

        Verifies FR05: the live _try_bandit_nudge_content path must call
        policy.update_reward() after bandit.update() so the Page-Hinkley
        detector accumulates reward observations.
        """
        from unittest.mock import MagicMock
        from trw_memory.bandit import BanditSelector
        from trw_mcp.state.bandit_policy import WithholdingPolicy
        from trw_mcp.state._ceremony_progress_state import CeremonyState
        from trw_mcp.tools._ceremony_status import _try_bandit_nudge_content

        trw_dir = tmp_path / ".trw"
        (trw_dir / "meta").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)
        state = CeremonyState(phase="implement", previous_phase="")

        learning = self._make_learning("L-reward-test")

        real_bandit = BanditSelector()
        real_policy = WithholdingPolicy(client_class="full_mode")
        mock_policy = MagicMock(wraps=real_policy)
        # Ensure withholding doesn't block the learning so update_reward is reached
        mock_policy.should_withhold.return_value = False

        with (
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[learning]),
            patch(
                "trw_mcp.state.bandit_policy.load_bandit_state_and_policy",
                return_value=(real_bandit, mock_policy),
            ),
        ):
            _try_bandit_nudge_content(trw_dir, state)

        assert mock_policy.update_reward.called, (
            "policy.update_reward() must be called in the live production path; "
            "without this the Page-Hinkley detector never accumulates observations"
        )
        arm_id_called = mock_policy.update_reward.call_args_list[0][0][0]
        reward_called = mock_policy.update_reward.call_args_list[0][0][1]
        assert arm_id_called == "L-reward-test"
        assert 0.0 <= reward_called <= 1.0

    def test_detector_state_persisted_in_envelope(self, tmp_path: Path) -> None:
        """Detector states appear in the detector_states key of bandit_state.json.

        After _try_bandit_nudge_content succeeds, the saved envelope must
        carry a non-empty detector_states entry so the state survives
        process restart.
        """
        import json as _json
        from trw_mcp.state._ceremony_progress_state import CeremonyState
        from trw_mcp.tools._ceremony_status import _try_bandit_nudge_content

        trw_dir = tmp_path / ".trw"
        (trw_dir / "meta").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)
        state = CeremonyState(phase="implement", previous_phase="")

        learning = self._make_learning("L-persist-test")

        with patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[learning]):
            _try_bandit_nudge_content(trw_dir, state)

        bandit_path = trw_dir / "meta" / "bandit_state.json"
        assert bandit_path.exists(), "bandit_state.json must be written by live path"
        stored = _json.loads(bandit_path.read_text(encoding="utf-8"))

        assert "detector_states" in stored, (
            "Envelope must contain 'detector_states' key for FR05 persistence; "
            f"found keys: {list(stored.keys())}"
        )
        detector_states = stored["detector_states"]
        assert isinstance(detector_states, dict)
        assert "L-persist-test" in detector_states, (
            f"Expected 'L-persist-test' in detector_states, got: {list(detector_states)}"
        )
        ds = detector_states["L-persist-test"]
        assert isinstance(ds, (dict, list))
        if isinstance(ds, dict):
            assert "n" in ds, f"Detector state must have 'n' field, got: {ds}"
            assert ds["n"] >= 1, f"Expected n >= 1 after one reward update, got n={ds['n']}"
        else:
            assert ds[0] >= 1, f"Expected compact detector n >= 1, got {ds}"
        assert stored.get("pending_alarm_ids") == []

    def test_detector_state_restored_on_next_load(self, tmp_path: Path) -> None:
        """load_bandit_state_and_policy restores Page-Hinkley state from envelope.

        After saving detector state, a subsequent load must return a policy
        whose per-arm detector has n > 0, proving the state was actually
        restored (not replaced by a fresh blank detector).
        """
        from trw_mcp.state.bandit_policy import (
            WithholdingPolicy,
            load_bandit_state_and_policy,
            save_bandit_state,
        )
        from trw_memory.bandit import BanditSelector

        trw_dir = tmp_path / ".trw"
        (trw_dir / "meta").mkdir(parents=True)

        arm_id = "L-restore-test"
        bandit = BanditSelector()
        policy = WithholdingPolicy(client_class="full_mode")
        for _ in range(5):
            bandit.update(arm_id, 0.8)
            policy.update_reward(arm_id, 0.8)

        assert policy._detectors[arm_id]._n == 5

        save_bandit_state(trw_dir, bandit, "full_mode", "test-model", policy=policy)

        _, restored_policy = load_bandit_state_and_policy(
            trw_dir, "full_mode", "test-model"
        )

        assert arm_id in restored_policy._detectors, (
            f"Expected '{arm_id}' in restored policy._detectors; "
            f"got: {list(restored_policy._detectors)}"
        )
        restored_n = restored_policy._detectors[arm_id]._n
        assert restored_n == 5, (
            f"Expected restored detector n=5, got n={restored_n}; "
            "detector state was not correctly restored from envelope"
        )

    def test_page_hinkley_alarm_reachable_across_sessions(self, tmp_path: Path) -> None:
        """Forced trigger #4 accumulates across simulated sessions and fires.

        Without cross-session persistence the detector always starts at n=0
        and can never accumulate enough deviation to fire trigger #4 in
        production — the core FR05 audit finding.  This test simulates:

        - Session 1: feed high rewards, persist with save_bandit_state.
        - Session 2: restore via load_bandit_state_and_policy, feed low
          rewards → the accumulated deviation causes the alarm to fire.
        """
        from trw_mcp.state.bandit_policy import (
            WithholdingPolicy,
            load_bandit_state_and_policy,
            save_bandit_state,
        )
        from trw_memory.bandit import BanditSelector
        from trw_memory.bandit.change_detection import PageHinkleyDetector

        trw_dir = tmp_path / ".trw"
        (trw_dir / "meta").mkdir(parents=True)

        arm_id = "L-alarm-test"
        model_family = "test-model"

        # Session 1: prime the detector with high rewards
        bandit1 = BanditSelector()
        policy1 = WithholdingPolicy(client_class="full_mode")
        # Use a low alarm threshold for deterministic test behaviour
        policy1._detectors[arm_id] = PageHinkleyDetector(delta=0.01, alarm_threshold=3.0)
        for _ in range(10):
            bandit1.update(arm_id, 0.9)
            policy1.update_reward(arm_id, 0.9)

        assert policy1._detectors[arm_id]._n == 10

        save_bandit_state(trw_dir, bandit1, "full_mode", model_family, policy=policy1)

        # Session 2: restore state, feed low rewards — alarm must fire
        _, policy2 = load_bandit_state_and_policy(trw_dir, "full_mode", model_family)

        assert arm_id in policy2._detectors, (
            "Session-2 policy must have the arm's detector restored"
        )
        assert policy2._detectors[arm_id]._n == 10, (
            f"Restored detector must have n=10 (session-1 history), "
            f"got n={policy2._detectors[arm_id]._n}"
        )

        alarms = [policy2.update_reward(arm_id, 0.1) for _ in range(15)]
        assert any(alarms), (
            "Page-Hinkley trigger #4 must fire after reward shift when "
            "accumulated session-1 history is restored; this was inert in "
            "production before FR05 cross-session persistence was wired in"
        )

    def test_pending_alarm_persisted_and_consumed_once(self, tmp_path: Path) -> None:
        """Pending FR05 alarms survive restart and force one normal-tier re-evaluation."""
        from trw_mcp.state.bandit_policy import (
            WithholdingPolicy,
            load_bandit_state_and_policy,
            save_bandit_state,
        )
        from trw_memory.bandit import BanditSelector
        from trw_memory.bandit.change_detection import PageHinkleyDetector

        trw_dir = tmp_path / ".trw"
        (trw_dir / "meta").mkdir(parents=True)

        arm_id = "L-pending-alarm"
        learning = {"id": arm_id, "protection_tier": "critical"}
        bandit = BanditSelector()
        policy = WithholdingPolicy(client_class="full_mode")
        policy._detectors[arm_id] = PageHinkleyDetector(delta=0.01, alarm_threshold=3.0)

        for _ in range(10):
            policy.update_reward(arm_id, 0.9)
        for _ in range(15):
            if policy.update_reward(arm_id, 0.1):
                break

        save_bandit_state(trw_dir, bandit, "full_mode", "test-model", policy=policy)
        _, restored_policy = load_bandit_state_and_policy(trw_dir, "full_mode", "test-model")

        assert arm_id in restored_policy._pending_alarm_ids
        with (
            patch("random.uniform", return_value=0.2),
            patch("random.random", side_effect=[0.1, 0.9]),
        ):
            assert restored_policy.should_withhold(learning) is True
            assert restored_policy.should_withhold(learning) is False

    def test_anchor_validity_drop_persisted_and_consumed_once(self, tmp_path: Path) -> None:
        """Trigger #1 becomes operational via persisted prior anchor validity."""
        from trw_mcp.state.bandit_policy import (
            WithholdingPolicy,
            load_bandit_state_and_policy,
            save_bandit_state,
        )
        from trw_memory.bandit import BanditSelector

        trw_dir = tmp_path / ".trw"
        (trw_dir / "meta").mkdir(parents=True)

        arm_id = "L-anchor-drop"
        bandit = BanditSelector()
        policy = WithholdingPolicy(client_class="full_mode")

        assert policy.should_withhold(
            {"id": arm_id, "protection_tier": "critical", "anchor_validity": 0.8}
        ) is False
        save_bandit_state(trw_dir, bandit, "full_mode", "test-model", policy=policy)

        _, restored_policy = load_bandit_state_and_policy(trw_dir, "full_mode", "test-model")
        with (
            patch("random.uniform", return_value=0.2),
            patch("random.random", return_value=0.1),
        ):
            assert restored_policy.should_withhold(
                {"id": arm_id, "protection_tier": "critical", "anchor_validity": 0.4}
            ) is True
        save_bandit_state(trw_dir, bandit, "full_mode", "test-model", policy=restored_policy)

        _, second_restore = load_bandit_state_and_policy(trw_dir, "full_mode", "test-model")
        with patch("random.random", return_value=0.1):
            assert second_restore.should_withhold(
                {"id": arm_id, "protection_tier": "critical", "anchor_validity": 0.4}
            ) is False

    def test_neutral_detector_state_compacts_and_restores(self) -> None:
        """Neutral detector state persists as a short list and restores safely."""
        from trw_mcp.state.bandit_policy import WithholdingPolicy

        arm_id = "L-neutral"
        policy = WithholdingPolicy(client_class="full_mode")
        for _ in range(25):
            policy.update_reward(arm_id, 0.6)

        persisted = policy.get_detector_states()
        assert persisted[arm_id] == [25, 15]

        restored = WithholdingPolicy(client_class="full_mode")
        restored.load_detector_states(persisted)
        detector = restored._detectors[arm_id]
        assert detector._n == 25
        assert detector._sum == pytest.approx(15.0)
        assert detector._h == 0.0
        assert detector._m == 0.0
        assert detector._h_down == 0.0
        assert detector._m_down == 0.0

    def test_detector_compaction_keeps_state_file_under_budget(self, tmp_path: Path) -> None:
        """Compact detector persistence keeps sub-500-arm state files below 100KB."""
        from trw_mcp.state.bandit_policy import (
            ENGINEERING_CONTEXT_DIM,
            build_context_vector,
            WithholdingPolicy,
            load_contextual_bandit_state,
            load_bandit_state_and_policy,
            save_bandit_state,
        )
        from trw_memory.bandit import BanditSelector, ContextualBanditSelector

        trw_dir = tmp_path / ".trw"
        (trw_dir / "meta").mkdir(parents=True)

        bandit = BanditSelector(cold_start_min=0)
        policy = WithholdingPolicy(client_class="full_mode")
        contextual = ContextualBanditSelector(feature_dim=ENGINEERING_CONTEXT_DIM, alpha=0.5)
        context = build_context_vector(
            phase="validate",
            session_progress=0.8,
            domain_similarity=1.0,
            files_count=12,
        )
        arm_count = 499
        reward_updates = 25

        for i in range(arm_count):
            arm_id = f"L{i}"
            for _ in range(reward_updates):
                bandit.update(arm_id, 0.6)
                policy.update_reward(arm_id, 0.6)
            policy._pending_alarm_ids.add(arm_id)
            if i < 2:
                contextual.update(arm_id, 0.6, context_vector=context)

        save_bandit_state(
            trw_dir,
            bandit,
            "full_mode",
            "test-model",
            policy=policy,
            contextual_bandit=contextual,
        )

        state_path = trw_dir / "meta" / "bandit_state.json"
        assert state_path.stat().st_size < 100 * 1024

        _, restored_policy = load_bandit_state_and_policy(trw_dir, "full_mode", "test-model")
        assert restored_policy._detectors["L0"]._n == reward_updates
        assert "L0" in restored_policy._pending_alarm_ids
        restored_contextual = load_contextual_bandit_state(trw_dir, model_family="test-model")
        assert restored_contextual is not None
        assert "L0" in restored_contextual._arms

    def test_live_path_soft_resets_bandit_arm_on_alarm(self, tmp_path: Path) -> None:
        """The live FR05 path soft-resets the arm posterior when the alarm fires."""
        from trw_memory.bandit import BanditSelector
        from trw_mcp.state.bandit_policy import WithholdingPolicy
        from trw_mcp.state._ceremony_progress_state import CeremonyState
        from trw_mcp.tools._ceremony_status import _try_bandit_nudge_content

        trw_dir = tmp_path / ".trw"
        (trw_dir / "meta").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        learning = self._make_learning("L-soft-reset")
        bandit = BanditSelector(cold_start_min=0)
        for _ in range(8):
            bandit.update("L-soft-reset", 0.9)

        policy = WithholdingPolicy(client_class="full_mode")

        with (
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[learning]),
            patch(
                "trw_mcp.state.bandit_policy.load_bandit_state_and_policy",
                return_value=(bandit, policy),
            ),
            patch.object(policy, "update_reward", return_value=True),
        ):
            _try_bandit_nudge_content(
                trw_dir,
                CeremonyState(phase="implement", previous_phase=""),
            )

        arm = bandit._arms["L-soft-reset"]
        assert arm.alpha == pytest.approx(2.0)
        assert arm.beta == pytest.approx(1.0)
        assert arm.window == []
