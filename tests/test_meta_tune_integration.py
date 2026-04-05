"""Integration tests for meta-tune orchestrator — exercises execute_meta_tune()
against real temp filesystem state, not mocked internals.

Sprint 84: Meta-Learning Layer Verification.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from trw_mcp.tools.meta_tune import execute_meta_tune


def _make_learning(**overrides: object) -> dict[str, object]:
    """Minimal learning entry dict with sensible defaults."""
    base: dict[str, object] = {
        "id": "L-test001",
        "summary": "Test learning",
        "detail": "Full detail for test learning",
        "status": "active",
        "type": "pattern",
        "impact": 0.7,
        "anchors": [],
        "anchor_validity": 1.0,
        "outcome_correlation": "",
        "sessions_surfaced": 0,
        "session_count": 0,
        "protection_tier": "normal",
        "expires": "",
        "tags": [],
        "reviewed_at": "",
    }
    base.update(overrides)
    return base


def _setup_trw_dir(tmp_path: Path) -> Path:
    """Create a minimal .trw/ directory structure for testing."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "meta").mkdir()
    (trw_dir / "memory").mkdir()
    (trw_dir / "skills").mkdir(parents=True)
    return trw_dir


def _base_config(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "model_family": "test-model",
        "trw_version": "0.39.0",
        "shadow_mode": False,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Steps 1-3: Deterministic mutations
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeterministicMutations:
    """Test steps 1-3 mutate learnings correctly in non-shadow mode."""

    def test_step1_demotes_zero_validity(self, tmp_path: Path) -> None:
        trw_dir = _setup_trw_dir(tmp_path)
        entry = _make_learning(
            id="L-demote",
            anchors=[{"file": "gone.py", "symbol_name": "fn"}],
            anchor_validity=0.0,
        )
        learnings = [entry]
        report = execute_meta_tune(
            trw_dir, learnings=learnings, config=_base_config(), steps=[1],
        )
        assert entry["status"] == "obsolete"
        assert report.learnings_demoted >= 1
        step = report.steps[0]
        assert step.step == "validate_anchors"
        assert step.status == "ok"

    def test_step1_skips_no_anchors(self, tmp_path: Path) -> None:
        trw_dir = _setup_trw_dir(tmp_path)
        entry = _make_learning(id="L-noanchor")
        learnings = [entry]
        report = execute_meta_tune(
            trw_dir, learnings=learnings, config=_base_config(), steps=[1],
        )
        assert entry["status"] == "active"

    def test_step1_protects_human_reviewed(self, tmp_path: Path) -> None:
        trw_dir = _setup_trw_dir(tmp_path)
        entry = _make_learning(
            id="L-reviewed",
            anchors=[{"file": "gone.py", "symbol_name": "fn"}],
            anchor_validity=0.0,
            reviewed_at="2026-04-01T00:00:00Z",
        )
        learnings = [entry]
        report = execute_meta_tune(
            trw_dir, learnings=learnings, config=_base_config(), steps=[1],
        )
        # Human-reviewed entries must NOT be demoted (NFR03)
        assert entry["status"] == "active"

    def test_step2_expires_workaround(self, tmp_path: Path) -> None:
        trw_dir = _setup_trw_dir(tmp_path)
        entry = _make_learning(
            id="L-wa-old",
            type="workaround",
            expires="2020-01-01",
            protection_tier="normal",
        )
        learnings = [entry]
        report = execute_meta_tune(
            trw_dir, learnings=learnings, config=_base_config(), steps=[2],
        )
        assert entry["protection_tier"] == "low"

    def test_step2_ignores_non_workarounds(self, tmp_path: Path) -> None:
        trw_dir = _setup_trw_dir(tmp_path)
        entry = _make_learning(id="L-pattern", type="pattern", expires="2020-01-01")
        learnings = [entry]
        execute_meta_tune(trw_dir, learnings=learnings, config=_base_config(), steps=[2])
        # Patterns should not be expired
        assert entry.get("protection_tier") == "normal"

    def test_step3_promotes_confirmed_hypothesis(self, tmp_path: Path) -> None:
        trw_dir = _setup_trw_dir(tmp_path)
        entry = _make_learning(
            id="L-hypo",
            type="hypothesis",
            session_count=40,
            outcome_correlation="positive",
        )
        learnings = [entry]
        report = execute_meta_tune(
            trw_dir, learnings=learnings, config=_base_config(), steps=[3],
        )
        assert entry["type"] == "pattern"
        assert report.hypotheses_resolved >= 1

    def test_step3_removes_unconfirmed_hypothesis(self, tmp_path: Path) -> None:
        trw_dir = _setup_trw_dir(tmp_path)
        entry = _make_learning(
            id="L-hypo-dead",
            type="hypothesis",
            session_count=40,
            outcome_correlation="neutral",
        )
        learnings = [entry]
        execute_meta_tune(trw_dir, learnings=learnings, config=_base_config(), steps=[3])
        assert entry["status"] == "obsolete"

    def test_step3_leaves_young_hypotheses(self, tmp_path: Path) -> None:
        trw_dir = _setup_trw_dir(tmp_path)
        entry = _make_learning(
            id="L-hypo-young",
            type="hypothesis",
            session_count=5,
            outcome_correlation="neutral",
        )
        learnings = [entry]
        execute_meta_tune(trw_dir, learnings=learnings, config=_base_config(), steps=[3])
        assert entry["type"] == "hypothesis"
        assert entry["status"] == "active"


# ---------------------------------------------------------------------------
# Step selectivity and shadow mode
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStepControl:
    """Verify step selection and shadow mode behavior."""

    def test_step_selectivity(self, tmp_path: Path) -> None:
        """Only selected steps run; others are skipped."""
        trw_dir = _setup_trw_dir(tmp_path)
        learnings = [_make_learning()]
        report = execute_meta_tune(
            trw_dir, learnings=learnings, config=_base_config(), steps=[1, 3, 7],
        )
        statuses = {s.step: s.status for s in report.steps}
        assert statuses["validate_anchors"] == "ok"
        assert statuses["resolve_hypotheses"] == "ok"
        assert statuses["validate_workarounds"] == "skipped"
        assert statuses["compute_correlations"] == "skipped"
        assert statuses["graph_maintenance"] == "skipped"
        assert statuses["bandit_update"] == "skipped"
        assert statuses["prd_nudge_analysis"] == "skipped"
        assert statuses["team_sync_report"] == "skipped"

    def test_shadow_mode_no_mutations(self, tmp_path: Path) -> None:
        """Shadow mode produces a report but does not mutate learnings."""
        trw_dir = _setup_trw_dir(tmp_path)
        entry = _make_learning(
            id="L-shadow",
            anchors=[{"file": "gone.py", "symbol_name": "fn"}],
            anchor_validity=0.0,
            type="workaround",
            expires="2020-01-01",
        )
        original = deepcopy(entry)
        learnings = [entry]
        report = execute_meta_tune(
            trw_dir,
            learnings=learnings,
            config=_base_config(shadow_mode=True),
            steps=[1, 2],
            shadow_mode=True,
        )
        # Entry should be unchanged
        assert entry["status"] == original["status"]
        assert entry["protection_tier"] == original["protection_tier"]
        # But report should show actions would have been taken
        assert report.steps[0].actions_taken > 0 or report.steps[1].actions_taken > 0

    def test_all_nine_steps_run_by_default(self, tmp_path: Path) -> None:
        """When steps=None, all 9 steps run."""
        trw_dir = _setup_trw_dir(tmp_path)
        report = execute_meta_tune(
            trw_dir, learnings=[], config=_base_config(shadow_mode=True),
        )
        assert len(report.steps) == 9
        step_names = [s.step for s in report.steps]
        assert "validate_anchors" in step_names
        assert "team_sync_report" in step_names


# ---------------------------------------------------------------------------
# Step 7: meta.yaml synthesis
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMetaYamlGeneration:
    """Test that step 7 generates meta.yaml correctly."""

    def test_meta_yaml_written(self, tmp_path: Path) -> None:
        trw_dir = _setup_trw_dir(tmp_path)
        learnings = [
            _make_learning(id=f"L-{i}", domain=["auth"], sessions_surfaced=20)
            for i in range(3)
        ]
        config = _base_config(shadow_mode=False)
        report = execute_meta_tune(
            trw_dir, learnings=learnings, config=config, steps=[7],
        )
        step = next(s for s in report.steps if s.step == "synthesize_artifacts")
        assert step.status == "ok"
        meta_path = trw_dir / "meta.yaml"
        assert meta_path.exists()


# ---------------------------------------------------------------------------
# Step 6: Bandit state persistence
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBanditStatePersistence:
    """Test that step 6 writes bandit_state.json with wrapped format."""

    def test_bandit_state_written(self, tmp_path: Path) -> None:
        trw_dir = _setup_trw_dir(tmp_path)
        learnings = [
            _make_learning(id=f"L-b{i}", outcome_correlation="positive")
            for i in range(5)
        ]
        config = _base_config(model_family="claude-4", shadow_mode=False)
        execute_meta_tune(trw_dir, learnings=learnings, config=config, steps=[6])

        state_path = trw_dir / "meta" / "bandit_state.json"
        assert state_path.exists()
        state = json.loads(state_path.read_text())
        # Verify wrapped format (PRD-CORE-105 P0)
        assert "bandit" in state
        assert "model_family" in state
        assert state["model_family"] == "claude-4"
        assert "quarantined" in state

    def test_model_family_change_quarantines(self, tmp_path: Path) -> None:
        trw_dir = _setup_trw_dir(tmp_path)
        learnings = [_make_learning(id="L-q1", outcome_correlation="positive")]

        # First run with model A
        config_a = _base_config(model_family="model-a", shadow_mode=False)
        execute_meta_tune(trw_dir, learnings=learnings, config=config_a, steps=[6])

        # Second run with model B
        config_b = _base_config(model_family="model-b", shadow_mode=False)
        execute_meta_tune(trw_dir, learnings=learnings, config=config_b, steps=[6])

        state_path = trw_dir / "meta" / "bandit_state.json"
        state = json.loads(state_path.read_text())
        assert state["model_family"] == "model-b"
        # Old model-a posteriors should be quarantined
        assert "model-a" in state.get("quarantined", {})


# ---------------------------------------------------------------------------
# Step 8: PRD nudge analysis
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPrdNudgeAnalysis:
    """Test step 8 identifies PRD-linked learning effectiveness."""

    def test_prd_linked_learnings_detected(self, tmp_path: Path) -> None:
        trw_dir = _setup_trw_dir(tmp_path)
        learnings = [
            _make_learning(id="L-prd1", tags=["PRD-CORE-110", "meta-learning"], outcome_correlation="positive"),
            _make_learning(id="L-prd2", tags=["PRD-CORE-105"], outcome_correlation="negative"),
            _make_learning(id="L-noprd", tags=["testing"]),
        ]
        report = execute_meta_tune(
            trw_dir, learnings=learnings, config=_base_config(), steps=[8],
        )
        step = next(s for s in report.steps if s.step == "prd_nudge_analysis")
        assert step.status == "ok"
        assert step.actions_taken == 2  # 2 PRD-linked learnings
        assert "1" in step.details  # 1 effective
        assert "flagged" in step.details  # 1 flagged


# ---------------------------------------------------------------------------
# Full pipeline smoke test
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFullPipeline:
    """Smoke test running all 9 steps on mixed learnings."""

    def test_full_pipeline_mixed_learnings(self, tmp_path: Path) -> None:
        trw_dir = _setup_trw_dir(tmp_path)
        learnings = [
            # Will be demoted by step 1
            _make_learning(
                id="L-dead-anchor",
                anchors=[{"file": "gone.py", "symbol_name": "fn"}],
                anchor_validity=0.0,
            ),
            # Will be expired by step 2
            _make_learning(
                id="L-old-wa",
                type="workaround",
                expires="2020-01-01",
                protection_tier="normal",
            ),
            # Will be promoted by step 3
            _make_learning(
                id="L-hypo-good",
                type="hypothesis",
                session_count=50,
                outcome_correlation="positive",
            ),
            # Normal learning — passes through unchanged
            _make_learning(
                id="L-normal",
                type="pattern",
                sessions_surfaced=5,
            ),
        ]

        report = execute_meta_tune(
            trw_dir,
            learnings=learnings,
            config=_base_config(shadow_mode=False),
        )

        assert len(report.steps) == 9
        assert report.learnings_demoted >= 1
        assert report.hypotheses_resolved >= 1

        # Verify mutations actually happened
        dead_anchor = next(e for e in learnings if e["id"] == "L-dead-anchor")
        assert dead_anchor["status"] == "obsolete"

        old_wa = next(e for e in learnings if e["id"] == "L-old-wa")
        assert old_wa["protection_tier"] == "low"

        hypo = next(e for e in learnings if e["id"] == "L-hypo-good")
        assert hypo["type"] == "pattern"

        normal = next(e for e in learnings if e["id"] == "L-normal")
        assert normal["status"] == "active"
        assert normal["type"] == "pattern"
