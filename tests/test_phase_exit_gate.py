"""Tests for the phase EXIT gate wired into ``update_run_phase``.

These tests drive the REAL ``update_run_phase`` path (never mocked) to prove
the previously-inert ``check_phase_exit`` gate is now active:

  - lenient (DEFAULT): an unmet gate WARNS and PROCEEDS (non-breaking).
  - strict: an unmet gate RAISES StateError and the phase is NOT written.
  - a valid forward transition PASSES in both modes with no warning.
  - a tier-skipped phase is NOT enforced even in strict mode.

The gate runs against the phase being LEFT (the run's current phase), so we
seed run.yaml at the phase whose exit criteria we want to exercise.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog
from structlog.testing import capture_logs

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig, reload_config
from trw_mcp.models.run import Phase
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.phase import update_run_phase

_GATE_EVENT = "phase_exit_gate_unmet"


@pytest.fixture(autouse=True)
def _restore_config() -> Iterator[None]:
    """Each test injects its own enforcement mode; reset the singleton after."""
    yield
    reload_config(None)


def _setup_run(
    tmp_path: Path,
    phase: str,
    *,
    phase_requirements: dict[str, list[str]] | None = None,
    complexity_class: str | None = None,
) -> Path:
    """Create a run directory whose run.yaml is at ``phase``."""
    meta = tmp_path / "meta"
    meta.mkdir(parents=True)
    data: dict[str, object] = {
        "run_id": "gate-test",
        "task": "gate-test-task",
        "status": "active",
        "phase": phase,
    }
    if complexity_class is not None:
        data["complexity_class"] = complexity_class
    if phase_requirements is not None:
        data["phase_requirements"] = phase_requirements
    FileStateWriter().write_yaml(meta / "run.yaml", data)
    return tmp_path


def _current_phase(run_path: Path) -> str:
    data = FileStateReader().read_yaml(run_path / "meta" / "run.yaml")
    return str(data["phase"])


class TestLenientWarnsButProceeds:
    """Default lenient posture: unmet gate is non-breaking."""

    def test_invalid_plan_exit_writes_and_warns(self, tmp_path: Path) -> None:
        # PLAN exit requires plan.md (error-severity when missing). No plan.md
        # exists here, so the gate fails — but lenient must still advance.
        reload_config(TRWConfig(phase_gate_enforcement="lenient"))
        run_path = _setup_run(tmp_path, "plan")

        structlog.configure(
            processors=[structlog.testing.LogCapture()],
            wrapper_class=structlog.make_filtering_bound_logger(0),
        )
        with capture_logs() as logs:
            result = update_run_phase(run_path, Phase.DELIVER)

        # Phase write SUCCEEDED despite the unmet gate (non-breaking).
        assert result is True
        assert _current_phase(run_path) == "deliver"

        # A gate warning was emitted naming the unmet 'plan' phase.
        warnings = [e for e in logs if e.get("event") == _GATE_EVENT]
        assert len(warnings) == 1, logs
        assert warnings[0]["from_phase"] == "plan"
        assert warnings[0]["failures"] >= 1
        assert "plan.md" in warnings[0]["detail"]


class TestStrictBlocks:
    """Strict posture: unmet gate raises and the write is rejected."""

    def test_invalid_plan_exit_raises_and_does_not_write(self, tmp_path: Path) -> None:
        reload_config(TRWConfig(phase_gate_enforcement="strict"))
        run_path = _setup_run(tmp_path, "plan")

        with pytest.raises(StateError) as exc_info:
            update_run_phase(run_path, Phase.DELIVER)

        # The blocking error names the leaving phase and the missing artifact.
        assert "plan" in str(exc_info.value)
        assert exc_info.value.context["from_phase"] == "plan"

        # The phase was NOT advanced — still at 'plan'.
        assert _current_phase(run_path) == "plan"


class TestValidTransitionPasses:
    """A satisfied gate proceeds with no warning, in both modes."""

    @pytest.mark.parametrize("mode", ["lenient", "strict"])
    def test_valid_plan_exit_advances_no_warning(self, tmp_path: Path, mode: str) -> None:
        reload_config(TRWConfig(phase_gate_enforcement=mode))
        run_path = _setup_run(tmp_path, "plan")
        # Provide the plan.md the PLAN exit gate requires -> gate passes
        # (no governing PRDs => advisory warning only, valid stays True).
        reports = run_path / "reports"
        reports.mkdir(parents=True)
        (reports / "plan.md").write_text("# Plan\n\nReal plan content.\n", encoding="utf-8")

        structlog.configure(
            processors=[structlog.testing.LogCapture()],
            wrapper_class=structlog.make_filtering_bound_logger(0),
        )
        with capture_logs() as logs:
            result = update_run_phase(run_path, Phase.IMPLEMENT)

        assert result is True
        assert _current_phase(run_path) == "implement"
        # No gate warning for a valid transition.
        assert [e for e in logs if e.get("event") == _GATE_EVENT] == []


class TestTierSkipNotBlocked:
    """A phase the active tier SKIPS is not enforced, even in strict mode."""

    def test_minimal_tier_skipped_phase_not_blocked(self, tmp_path: Path) -> None:
        # MINIMAL skips RESEARCH/PLAN/REVIEW. A run sitting at 'research'
        # (a skipped phase for this tier) advancing forward must NOT be
        # blocked by the research exit gate, even under strict enforcement.
        reload_config(TRWConfig(phase_gate_enforcement="strict"))
        run_path = _setup_run(
            tmp_path,
            "research",
            complexity_class="MINIMAL",
            phase_requirements={
                "mandatory": ["IMPLEMENT", "VALIDATE", "DELIVER"],
                "optional": [],
                "skipped": ["RESEARCH", "PLAN", "REVIEW"],
            },
        )

        structlog.configure(
            processors=[structlog.testing.LogCapture()],
            wrapper_class=structlog.make_filtering_bound_logger(0),
        )
        with capture_logs() as logs:
            # No StateError raised despite strict mode -> tier skip honored.
            result = update_run_phase(run_path, Phase.IMPLEMENT)

        assert result is True
        assert _current_phase(run_path) == "implement"
        # The gate did not even warn — it was skipped for the tier.
        assert [e for e in logs if e.get("event") == _GATE_EVENT] == []
        assert any(e.get("event") == "phase_exit_gate_skipped_for_tier" for e in logs)

    def test_non_skipped_phase_still_enforced_in_strict(self, tmp_path: Path) -> None:
        # Same MINIMAL tier, but leaving 'plan' is NOT a skipped phase per the
        # (intentionally non-skipping) phase_requirements here -> gate enforces.
        reload_config(TRWConfig(phase_gate_enforcement="strict"))
        run_path = _setup_run(
            tmp_path,
            "plan",
            complexity_class="STANDARD",
            phase_requirements={
                "mandatory": ["PLAN", "IMPLEMENT", "VALIDATE", "REVIEW", "DELIVER"],
                "optional": [],
                "skipped": ["RESEARCH"],
            },
        )

        with pytest.raises(StateError):
            update_run_phase(run_path, Phase.IMPLEMENT)
        assert _current_phase(run_path) == "plan"
