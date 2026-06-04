"""F13 regression: ``update_run_phase`` mirrors the phase into CeremonyState.

Defect F13: ``set_ceremony_phase`` was defined + re-exported but never called,
so ``CeremonyState.phase`` was stuck at its ``"early"`` default forever. The
status line always emitted ``phase=early`` and phase-aware nudge dedup
(``is_nudge_eligible``, keyed on the current phase) never worked.

These tests drive the *real* ``update_run_phase`` and assert that after a
transition ``CeremonyState.phase`` reflects the NEW phase (proving it is no
longer the constant ``"early"`` default) and that nudge eligibility /
status keys on the real phase.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.run import Phase
from trw_mcp.state._ceremony_progress_state import (
    is_nudge_eligible,
    read_ceremony_state,
    record_nudge_shown,
)
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.state.phase import update_run_phase


def _setup_run(tmp_path: Path, phase: str = "research") -> Path:
    """Create a minimal run directory with run.yaml at ``phase``."""
    meta = tmp_path / "run" / "meta"
    meta.mkdir(parents=True)
    FileStateWriter().write_yaml(
        meta / "run.yaml",
        {"run_id": "test-run", "task": "test-task", "status": "active", "phase": phase},
    )
    return tmp_path / "run"


@pytest.fixture
def trw_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``resolve_trw_dir`` at an isolated tmp ``.trw`` for the sync write.

    ``_sync_ceremony_phase`` looks up ``resolve_trw_dir`` from
    ``trw_mcp.state._paths`` at call time, so patching it there is sufficient.
    """
    d = tmp_path / ".trw"
    (d / "context").mkdir(parents=True)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: d)
    return d


class TestCeremonyPhaseMirrored:
    """update_run_phase keeps CeremonyState.phase in sync with the run phase."""

    def test_phase_not_constant_early_after_transition(self, tmp_path: Path, trw_dir: Path) -> None:
        """The core F13 assertion: ceremony phase is no longer stuck at 'early'."""
        run_path = _setup_run(tmp_path, "research")
        # Baseline: no state file yet -> default 'early'.
        assert read_ceremony_state(trw_dir).phase == "early"

        assert update_run_phase(run_path, Phase.VALIDATE) is True

        state = read_ceremony_state(trw_dir)
        assert state.phase == "validate"
        assert state.phase != "early"
        # previous_phase records what we moved away from (the prior ceremony value).
        assert state.previous_phase == "early"

    def test_implement_maps_to_implement(self, tmp_path: Path, trw_dir: Path) -> None:
        run_path = _setup_run(tmp_path, "research")
        update_run_phase(run_path, Phase.IMPLEMENT)
        assert read_ceremony_state(trw_dir).phase == "implement"

    def test_review_maps_to_review(self, tmp_path: Path, trw_dir: Path) -> None:
        run_path = _setup_run(tmp_path, "validate")
        update_run_phase(run_path, Phase.REVIEW)
        assert read_ceremony_state(trw_dir).phase == "review"

    def test_deliver_maps_to_deliver(self, tmp_path: Path, trw_dir: Path) -> None:
        run_path = _setup_run(tmp_path, "review")
        update_run_phase(run_path, Phase.DELIVER)
        assert read_ceremony_state(trw_dir).phase == "deliver"

    def test_research_and_plan_collapse_to_early(self, tmp_path: Path, trw_dir: Path) -> None:
        """RESEARCH/PLAN map to 'early' so early-phase nudge rules apply."""
        run_path = _setup_run(tmp_path, "research")
        # research -> plan: both collapse to 'early', so ceremony phase stays 'early'.
        assert update_run_phase(run_path, Phase.PLAN) is True
        assert read_ceremony_state(trw_dir).phase == "early"

    def test_skipped_transition_does_not_change_phase(self, tmp_path: Path, trw_dir: Path) -> None:
        """A backward (rejected) transition must not touch ceremony phase."""
        run_path = _setup_run(tmp_path, "review")
        # First advance to deliver so a real ceremony phase is set.
        update_run_phase(run_path, Phase.DELIVER)
        assert read_ceremony_state(trw_dir).phase == "deliver"
        # Backward jump is rejected; ceremony phase must remain 'deliver'.
        assert update_run_phase(run_path, Phase.IMPLEMENT) is False
        assert read_ceremony_state(trw_dir).phase == "deliver"


class TestNudgeEligibilityKeysOnRealPhase:
    """Phase-aware nudge dedup now keys on the real phase, not constant 'early'."""

    def test_nudge_dedup_uses_real_phase(self, tmp_path: Path, trw_dir: Path) -> None:
        """A learning shown in 'validate' is ineligible in 'validate', eligible elsewhere.

        Before F13 the recorded phase would always be 'early', so dedup could
        never key on 'validate'. This proves the recorded/keyed phase is real.
        """
        run_path = _setup_run(tmp_path, "research")
        update_run_phase(run_path, Phase.VALIDATE)

        state = read_ceremony_state(trw_dir)
        assert state.phase == "validate"

        # Record a nudge impression at the *real* current phase.
        record_nudge_shown(trw_dir, "L-1", state.phase, turn=state.tool_call_counter)
        state = read_ceremony_state(trw_dir)

        # Same phase -> already shown -> ineligible. Proves the key is 'validate'.
        assert is_nudge_eligible(state, "L-1", "validate") is False
        # A different phase -> eligible (it was only shown in 'validate').
        assert is_nudge_eligible(state, "L-1", "review") is True
        # And critically NOT recorded under the stale 'early' key.
        assert "early" not in state.nudge_history["L-1"]["phases_shown"]
        assert state.nudge_history["L-1"]["phases_shown"] == ["validate"]
