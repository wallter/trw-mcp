"""Tests for PRD-CORE-013: Phase Reversion & Refactor-First Workflow.

Covers: ReversionTrigger enum, phase_revert event handling,
reversion frequency metrics, config fields, integration lifecycle.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.run import PHASE_ORDER, Phase, ReversionTrigger
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    import trw_mcp.tools.orchestration as orch_mod
    monkeypatch.setattr(orch_mod, "_config", orch_mod.TRWConfig())
    return tmp_path


@pytest.fixture
def tools(tmp_path: Path) -> dict[str, object]:
    """Create orchestration tools on a fresh FastMCP server."""
    from fastmcp import FastMCP
    from trw_mcp.tools.orchestration import register_orchestration_tools

    srv = FastMCP("test-reversion")
    register_orchestration_tools(srv)
    return {t.name: t for t in srv._tool_manager._tools.values()}


@pytest.fixture
def run_path(tools: dict[str, object]) -> str:
    """Initialize a run and return its path."""
    result = tools["trw_init"].fn(task_name="reversion-test")
    return result["run_path"]


# ---------------------------------------------------------------------------
# ReversionTrigger enum (FR02)
# ---------------------------------------------------------------------------


class TestReversionTriggerEnum:
    """PRD-CORE-013-FR02: ReversionTrigger enum."""

    def test_all_trigger_values(self) -> None:
        """All 6 trigger values are valid enum members."""
        expected = {
            "refactor_needed", "architecture_mismatch", "new_dependency",
            "test_strategy_change", "scope_change", "other",
        }
        actual = {t.value for t in ReversionTrigger}
        assert actual == expected

    def test_is_str_enum(self) -> None:
        """ReversionTrigger inherits from (str, Enum) for YAML compat."""
        assert isinstance(ReversionTrigger.REFACTOR_NEEDED, str)
        assert ReversionTrigger.REFACTOR_NEEDED == "refactor_needed"

    def test_classify_known_trigger(self) -> None:
        """Known trigger string maps to correct enum."""
        assert ReversionTrigger.classify("refactor_needed") == ReversionTrigger.REFACTOR_NEEDED
        assert ReversionTrigger.classify("scope_change") == ReversionTrigger.SCOPE_CHANGE

    def test_classify_unknown_as_other(self) -> None:
        """Unrecognized trigger string classified as OTHER."""
        assert ReversionTrigger.classify("performance_issue") == ReversionTrigger.OTHER
        assert ReversionTrigger.classify("") == ReversionTrigger.OTHER


class TestPhaseOrder:
    """Phase ordering for reversion validation."""

    def test_phase_order_complete(self) -> None:
        """All 6 phases have ordering entries."""
        for phase in Phase:
            assert phase.value in PHASE_ORDER

    def test_phase_order_monotonic(self) -> None:
        """Phase order is strictly increasing."""
        phases = ["research", "plan", "implement", "validate", "review", "deliver"]
        for i in range(len(phases) - 1):
            assert PHASE_ORDER[phases[i]] < PHASE_ORDER[phases[i + 1]]


# ---------------------------------------------------------------------------
# Phase Revert Event Validation (FR01, FR03)
# ---------------------------------------------------------------------------


class TestPhaseRevertEvent:
    """PRD-CORE-013-FR01: phase_revert event handling."""

    def test_valid_backward_reversion(
        self, tools: dict[str, object], run_path: str,
    ) -> None:
        """IMPLEMENT to PLAN is accepted and phase updated."""
        # Set phase to implement first
        writer = FileStateWriter()
        reader = FileStateReader()
        run_yaml = Path(run_path) / "meta" / "run.yaml"
        state = reader.read_yaml(run_yaml)
        state["phase"] = "implement"
        writer.write_yaml(run_yaml, state)

        result = tools["trw_event"].fn(
            event_type="phase_revert",
            run_path=run_path,
            data={
                "from_phase": "implement",
                "to_phase": "plan",
                "trigger": "refactor_needed",
                "reason": "Shared utility needs extraction",
            },
        )
        assert result["status"] == "event_logged"
        assert result["reversion_applied"] is True

        # Verify run.yaml phase updated
        updated_state = reader.read_yaml(run_yaml)
        assert updated_state["phase"] == "plan"

    def test_valid_multi_step_backward(
        self, tools: dict[str, object], run_path: str,
    ) -> None:
        """IMPLEMENT to RESEARCH is accepted (skipping PLAN)."""
        writer = FileStateWriter()
        reader = FileStateReader()
        run_yaml = Path(run_path) / "meta" / "run.yaml"
        state = reader.read_yaml(run_yaml)
        state["phase"] = "implement"
        writer.write_yaml(run_yaml, state)

        result = tools["trw_event"].fn(
            event_type="phase_revert",
            run_path=run_path,
            data={
                "from_phase": "implement",
                "to_phase": "research",
                "trigger": "architecture_mismatch",
                "reason": "Assumptions were incorrect",
            },
        )
        assert result["reversion_applied"] is True

        updated_state = reader.read_yaml(run_yaml)
        assert updated_state["phase"] == "research"

    def test_invalid_forward_reversion(
        self, tools: dict[str, object], run_path: str,
    ) -> None:
        """PLAN to IMPLEMENT is rejected (forward is not reversion)."""
        result = tools["trw_event"].fn(
            event_type="phase_revert",
            run_path=run_path,
            data={
                "from_phase": "plan",
                "to_phase": "implement",
                "trigger": "other",
                "reason": "Trying to go forward",
            },
        )
        assert result["reversion_applied"] is False

    def test_invalid_same_phase(
        self, tools: dict[str, object], run_path: str,
    ) -> None:
        """IMPLEMENT to IMPLEMENT is rejected (no-op reversion)."""
        result = tools["trw_event"].fn(
            event_type="phase_revert",
            run_path=run_path,
            data={
                "from_phase": "implement",
                "to_phase": "implement",
                "trigger": "other",
                "reason": "Same phase",
            },
        )
        assert result["reversion_applied"] is False

    def test_invalid_phase_values(
        self, tools: dict[str, object], run_path: str,
    ) -> None:
        """Invalid phase strings are rejected."""
        result = tools["trw_event"].fn(
            event_type="phase_revert",
            run_path=run_path,
            data={
                "from_phase": "nonexistent",
                "to_phase": "plan",
                "trigger": "other",
                "reason": "Bad phase",
            },
        )
        assert result["reversion_applied"] is False

    def test_event_logged_with_all_fields(
        self, tools: dict[str, object], run_path: str,
    ) -> None:
        """events.jsonl contains the phase_revert event with all required fields."""
        writer = FileStateWriter()
        reader = FileStateReader()
        run_yaml = Path(run_path) / "meta" / "run.yaml"
        state = reader.read_yaml(run_yaml)
        state["phase"] = "validate"
        writer.write_yaml(run_yaml, state)

        tools["trw_event"].fn(
            event_type="phase_revert",
            run_path=run_path,
            data={
                "from_phase": "validate",
                "to_phase": "implement",
                "trigger": "test_strategy_change",
                "reason": "Need integration tests",
            },
        )

        events = reader.read_jsonl(Path(run_path) / "meta" / "events.jsonl")
        revert_events = [e for e in events if e.get("event") == "phase_revert"]
        assert len(revert_events) >= 1
        evt = revert_events[-1]
        assert evt["from_phase"] == "validate"
        assert evt["to_phase"] == "implement"
        assert evt["trigger"] == "test_strategy_change"
        assert evt["reason"] == "Need integration tests"
        assert evt["reversion_status"] == "applied"
        assert evt["trigger_classified"] == "test_strategy_change"

    def test_unknown_trigger_classified_as_other(
        self, tools: dict[str, object], run_path: str,
    ) -> None:
        """Unknown trigger string is logged with warning and classified as OTHER."""
        writer = FileStateWriter()
        reader = FileStateReader()
        run_yaml = Path(run_path) / "meta" / "run.yaml"
        state = reader.read_yaml(run_yaml)
        state["phase"] = "implement"
        writer.write_yaml(run_yaml, state)

        tools["trw_event"].fn(
            event_type="phase_revert",
            run_path=run_path,
            data={
                "from_phase": "implement",
                "to_phase": "plan",
                "trigger": "performance_issue",
                "reason": "Unknown trigger test",
            },
        )

        events = reader.read_jsonl(Path(run_path) / "meta" / "events.jsonl")
        revert_events = [e for e in events if e.get("event") == "phase_revert"]
        evt = revert_events[-1]
        assert evt["trigger_classified"] == "other"
        assert "trigger_warning" in evt


# ---------------------------------------------------------------------------
# Reversion Frequency Metrics (FR07)
# ---------------------------------------------------------------------------


class TestReversionMetrics:
    """PRD-CORE-013-FR07: Reversion frequency metrics in trw_status."""

    def test_no_reversions(
        self, tools: dict[str, object], run_path: str,
    ) -> None:
        """count=0, rate=0.0, classification='healthy' with no reversions."""
        status = tools["trw_status"].fn(run_path=run_path)
        assert "reversions" in status
        rev = status["reversions"]
        assert rev["count"] == 0
        assert rev["rate"] == 0.0
        assert rev["classification"] == "healthy"
        assert rev["latest"] is None

    def test_healthy_rate(
        self, tools: dict[str, object], run_path: str,
    ) -> None:
        """1 reversion in 10 transitions = 0.10, 'healthy'."""
        writer = FileStateWriter()
        events_path = Path(run_path) / "meta" / "events.jsonl"

        # Add 9 phase_enter events
        for _ in range(9):
            writer.append_jsonl(events_path, {
                "ts": "2026-02-09T12:00:00Z",
                "event": "phase_enter",
                "phase": "implement",
            })

        # Set phase to implement for valid reversion
        reader = FileStateReader()
        run_yaml = Path(run_path) / "meta" / "run.yaml"
        state = reader.read_yaml(run_yaml)
        state["phase"] = "implement"
        writer.write_yaml(run_yaml, state)

        # Add 1 phase_revert event
        tools["trw_event"].fn(
            event_type="phase_revert",
            run_path=run_path,
            data={
                "from_phase": "implement",
                "to_phase": "plan",
                "trigger": "refactor_needed",
                "reason": "Test",
            },
        )

        status = tools["trw_status"].fn(run_path=run_path)
        rev = status["reversions"]
        assert rev["count"] == 1
        assert rev["rate"] == 0.1
        assert rev["classification"] == "healthy"

    def test_elevated_rate(
        self, tools: dict[str, object], run_path: str,
    ) -> None:
        """2 reversions in 8 transitions = 0.25, 'elevated'."""
        writer = FileStateWriter()
        events_path = Path(run_path) / "meta" / "events.jsonl"

        # Add 6 phase_enter events
        for _ in range(6):
            writer.append_jsonl(events_path, {
                "ts": "2026-02-09T12:00:00Z",
                "event": "phase_enter",
                "phase": "implement",
            })

        # Add 2 phase_revert events directly to JSONL
        for trigger in ["refactor_needed", "scope_change"]:
            writer.append_jsonl(events_path, {
                "ts": "2026-02-09T12:00:01Z",
                "event": "phase_revert",
                "from_phase": "implement",
                "to_phase": "plan",
                "trigger": trigger,
                "trigger_classified": trigger,
                "reversion_status": "applied",
                "reason": "Test",
            })

        status = tools["trw_status"].fn(run_path=run_path)
        rev = status["reversions"]
        assert rev["count"] == 2
        assert rev["rate"] == 0.25
        assert rev["classification"] == "elevated"

    def test_concerning_rate(
        self, tools: dict[str, object], run_path: str,
    ) -> None:
        """3 reversions in 10 transitions = 0.30, 'concerning'."""
        writer = FileStateWriter()
        events_path = Path(run_path) / "meta" / "events.jsonl"

        # Add 7 phase_enter events
        for _ in range(7):
            writer.append_jsonl(events_path, {
                "ts": "2026-02-09T12:00:00Z",
                "event": "phase_enter",
                "phase": "implement",
            })

        # Add 3 phase_revert events
        for _ in range(3):
            writer.append_jsonl(events_path, {
                "ts": "2026-02-09T12:00:01Z",
                "event": "phase_revert",
                "from_phase": "implement",
                "to_phase": "plan",
                "trigger_classified": "refactor_needed",
                "reversion_status": "applied",
                "reason": "Test",
            })

        status = tools["trw_status"].fn(run_path=run_path)
        rev = status["reversions"]
        assert rev["count"] == 3
        assert rev["rate"] == 0.3
        assert rev["classification"] == "concerning"

    def test_by_trigger_aggregation(
        self, tools: dict[str, object], run_path: str,
    ) -> None:
        """Trigger counts are correctly aggregated."""
        writer = FileStateWriter()
        events_path = Path(run_path) / "meta" / "events.jsonl"

        triggers = ["refactor_needed", "refactor_needed", "scope_change"]
        for t in triggers:
            writer.append_jsonl(events_path, {
                "ts": "2026-02-09T12:00:00Z",
                "event": "phase_revert",
                "trigger_classified": t,
                "reversion_status": "applied",
            })

        status = tools["trw_status"].fn(run_path=run_path)
        by_trigger = status["reversions"]["by_trigger"]
        assert by_trigger["refactor_needed"] == 2
        assert by_trigger["scope_change"] == 1

    def test_latest_event(
        self, tools: dict[str, object], run_path: str,
    ) -> None:
        """Most recent reversion is returned."""
        writer = FileStateWriter()
        reader = FileStateReader()
        run_yaml = Path(run_path) / "meta" / "run.yaml"
        state = reader.read_yaml(run_yaml)
        state["phase"] = "validate"
        writer.write_yaml(run_yaml, state)

        tools["trw_event"].fn(
            event_type="phase_revert",
            run_path=run_path,
            data={
                "from_phase": "validate",
                "to_phase": "plan",
                "trigger": "new_dependency",
                "reason": "Found missing dep",
            },
        )

        status = tools["trw_status"].fn(run_path=run_path)
        latest = status["reversions"]["latest"]
        assert latest is not None
        assert latest["from_phase"] == "validate"
        assert latest["to_phase"] == "plan"
        assert latest["trigger"] == "new_dependency"

    def test_configurable_thresholds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Custom config values change classification boundaries."""
        import trw_mcp.tools.orchestration as orch_mod
        from trw_mcp.models.config import TRWConfig
        from fastmcp import FastMCP
        from trw_mcp.tools.orchestration import register_orchestration_tools

        # Set custom thresholds
        custom_config = TRWConfig(
            reversion_rate_elevated=0.05,
            reversion_rate_concerning=0.10,
        )
        monkeypatch.setattr(orch_mod, "_config", custom_config)

        srv = FastMCP("test-custom-thresh")
        register_orchestration_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}

        init_result = tools["trw_init"].fn(task_name="thresh-task")
        rp = init_result["run_path"]

        # Add events: 1 revert + 9 enters = rate 0.10
        writer = FileStateWriter()
        events_path = Path(rp) / "meta" / "events.jsonl"
        for _ in range(9):
            writer.append_jsonl(events_path, {
                "ts": "2026-02-09T12:00:00Z",
                "event": "phase_enter",
            })
        writer.append_jsonl(events_path, {
            "ts": "2026-02-09T12:00:01Z",
            "event": "phase_revert",
            "trigger_classified": "other",
            "reversion_status": "applied",
        })

        status = tools["trw_status"].fn(run_path=rp)
        # With threshold 0.10 for concerning, rate 0.10 should be concerning
        assert status["reversions"]["classification"] == "concerning"


# ---------------------------------------------------------------------------
# Config Fields
# ---------------------------------------------------------------------------


class TestConfigReversionFields:
    """PRD-CORE-013: Config fields for reversion thresholds."""

    def test_default_elevated(self) -> None:
        from trw_mcp.models.config import TRWConfig
        config = TRWConfig()
        assert config.reversion_rate_elevated == 0.15

    def test_default_concerning(self) -> None:
        from trw_mcp.models.config import TRWConfig
        config = TRWConfig()
        assert config.reversion_rate_concerning == 0.30

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_REVERSION_RATE_ELEVATED", "0.20")
        from trw_mcp.models.config import TRWConfig
        config = TRWConfig()
        assert config.reversion_rate_elevated == 0.20


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------


class TestPhaseReversionIntegration:
    """Integration tests for full phase reversion lifecycle."""

    def test_revert_then_advance_lifecycle(
        self, tools: dict[str, object], run_path: str,
    ) -> None:
        """Revert from IMPLEMENT to PLAN, then advance forward again."""
        writer = FileStateWriter()
        reader = FileStateReader()
        run_yaml = Path(run_path) / "meta" / "run.yaml"

        # Advance to IMPLEMENT
        state = reader.read_yaml(run_yaml)
        state["phase"] = "implement"
        writer.write_yaml(run_yaml, state)

        # Revert to PLAN
        result = tools["trw_event"].fn(
            event_type="phase_revert",
            run_path=run_path,
            data={
                "from_phase": "implement",
                "to_phase": "plan",
                "trigger": "refactor_needed",
                "reason": "Need to restructure module boundaries",
            },
        )
        assert result["reversion_applied"] is True

        # Verify phase is now PLAN
        state = reader.read_yaml(run_yaml)
        assert state["phase"] == "plan"

        # Add enough forward transitions for a healthy rate
        events_path = Path(run_path) / "meta" / "events.jsonl"
        for phase in ["plan", "implement", "validate", "review", "deliver",
                       "research", "plan", "implement", "validate"]:
            writer.append_jsonl(events_path, {
                "ts": "2026-02-09T12:00:00Z",
                "event": "phase_enter",
                "phase": phase,
            })

        # Verify final state: 1 revert + 9 enters = rate 0.10
        status = tools["trw_status"].fn(run_path=run_path)
        assert status["reversions"]["count"] == 1
        assert status["reversions"]["classification"] == "healthy"

    def test_trw_status_includes_reversions(
        self, tools: dict[str, object], run_path: str,
    ) -> None:
        """End-to-end: trw_status returns reversions section after reversion."""
        writer = FileStateWriter()
        reader = FileStateReader()
        run_yaml = Path(run_path) / "meta" / "run.yaml"
        state = reader.read_yaml(run_yaml)
        state["phase"] = "implement"
        writer.write_yaml(run_yaml, state)

        tools["trw_event"].fn(
            event_type="phase_revert",
            run_path=run_path,
            data={
                "from_phase": "implement",
                "to_phase": "plan",
                "trigger": "architecture_mismatch",
                "reason": "API design conflict",
            },
        )

        status = tools["trw_status"].fn(run_path=run_path)
        rev = status["reversions"]
        assert rev["count"] == 1
        assert rev["latest"]["trigger"] == "architecture_mismatch"
        assert "by_trigger" in rev
