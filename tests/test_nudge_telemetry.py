"""Tests for PRD-QUAL-058: Nudge Telemetry Event Emission (FR04, FR05).

Covers:
- FR04: log_nudge_event emits nudge_shown events to session events JSONL
- FR04: nudge_shown event schema matches PRD specification
- FR04: log_nudge_event is called from append_ceremony_nudge when a learning is selected
- FR05: trw_deliver_complete event includes nudge_summary from CeremonyState
"""

from __future__ import annotations

import json
from pathlib import Path

from trw_mcp.state._nudge_state import (
    CeremonyState,
    record_nudge_shown,
    write_ceremony_state,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_trw_dir(tmp_path: Path) -> Path:
    """Create .trw/context/ directory and return the .trw path."""
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    return trw_dir


def _read_events_jsonl(path: Path) -> list[dict[str, object]]:
    """Read all events from a JSONL file."""
    if not path.exists():
        return []
    events: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").strip().split("\n"):
        if line.strip():
            events.append(json.loads(line))
    return events


# ===========================================================================
# FR04: log_nudge_event emits nudge_shown events
# ===========================================================================


class TestLogNudgeEvent:
    """FR04: log_nudge_event writes structured nudge_shown events."""

    def test_nudge_shown_event_emitted(self, tmp_path: Path) -> None:
        """log_nudge_event writes a nudge_shown event to events.jsonl."""
        from trw_mcp.tools._legacy_ceremony_nudge import log_nudge_event

        events_path = tmp_path / "events.jsonl"
        log_nudge_event(
            events_path,
            learning_id="L-abc123",
            phase="IMPLEMENT",
            is_fallback=False,
        )

        events = _read_events_jsonl(events_path)
        assert len(events) == 1
        evt = events[0]
        assert evt["event"] == "nudge_shown"
        assert evt["learning_id"] == "L-abc123"
        assert evt["phase"] == "IMPLEMENT"
        assert "ts" in evt

    def test_nudge_shown_event_schema(self, tmp_path: Path) -> None:
        """nudge_shown event includes data field with learning_id, phase, turn, surface_type."""
        from trw_mcp.tools._legacy_ceremony_nudge import log_nudge_event

        events_path = tmp_path / "events.jsonl"
        log_nudge_event(
            events_path,
            learning_id="L-def456",
            phase="VALIDATE",
            is_fallback=True,
            turn=7,
            surface_type="nudge",
        )

        events = _read_events_jsonl(events_path)
        assert len(events) == 1
        evt = events[0]
        data = evt.get("data")
        assert isinstance(data, dict)
        assert data["learning_id"] == "L-def456"
        assert data["phase"] == "VALIDATE"
        assert data["turn"] == 7
        assert data["surface_type"] == "nudge"

    def test_nudge_event_fallback_field(self, tmp_path: Path) -> None:
        """nudge_shown event includes fallback indicator."""
        from trw_mcp.tools._legacy_ceremony_nudge import log_nudge_event

        events_path = tmp_path / "events.jsonl"
        log_nudge_event(
            events_path,
            learning_id="L-xyz",
            phase="DELIVER",
            is_fallback=True,
        )

        events = _read_events_jsonl(events_path)
        assert events[0]["fallback"] is True

    def test_nudge_event_multiple_emissions(self, tmp_path: Path) -> None:
        """Multiple nudge_shown events append to the same JSONL file."""
        from trw_mcp.tools._legacy_ceremony_nudge import log_nudge_event

        events_path = tmp_path / "events.jsonl"
        for i in range(3):
            log_nudge_event(
                events_path,
                learning_id=f"L-{i:03d}",
                phase="IMPLEMENT",
                is_fallback=False,
            )

        events = _read_events_jsonl(events_path)
        assert len(events) == 3
        assert all(e["event"] == "nudge_shown" for e in events)

    def test_nudge_event_failopen(self, tmp_path: Path) -> None:
        """log_nudge_event does not raise when path is unwritable."""
        from trw_mcp.tools._legacy_ceremony_nudge import log_nudge_event

        # Path to a directory that doesn't exist and can't be created
        bad_path = tmp_path / "nonexistent" / "deep" / "path" / "events.jsonl"
        # Should not raise
        log_nudge_event(
            bad_path,
            learning_id="L-fail",
            phase="IMPLEMENT",
            is_fallback=False,
        )


# ===========================================================================
# FR04 (root cause): record_nudge_shown emits to session-events.jsonl
# ===========================================================================


class TestRecordNudgeShownEmitsSessionEvent:
    """FR04 root-cause wiring: record_nudge_shown appends nudge_shown to JSONL.

    These tests verify the root-cause fix — record_nudge_shown is the
    sole code path that runs when a nudge is shown to the agent, so a
    side-effect on session-events.jsonl there is the production emission
    point. Without this, eval-pipeline scorers only see the aggregate
    ceremony-state.json snapshot.
    """

    def test_emits_nudge_shown_event(self, tmp_path: Path) -> None:
        """record_nudge_shown appends a nudge_shown event to session-events.jsonl."""
        trw_dir = _setup_trw_dir(tmp_path)
        record_nudge_shown(trw_dir, "L-wire-001", "IMPLEMENT", turn=3)

        events = _read_events_jsonl(trw_dir / "context" / "session-events.jsonl")
        assert len(events) == 1
        evt = events[0]
        assert evt["event"] == "nudge_shown"
        assert evt["learning_id"] == "L-wire-001"
        assert evt["phase"] == "IMPLEMENT"
        data = evt["data"]
        assert isinstance(data, dict)
        assert data["learning_id"] == "L-wire-001"
        assert data["phase"] == "IMPLEMENT"
        assert data["turn"] == 3
        assert data["surface_type"] == "nudge"
        assert "ts" in evt

    def test_surface_type_propagates(self, tmp_path: Path) -> None:
        """Non-default surface_type is recorded in the emitted event."""
        trw_dir = _setup_trw_dir(tmp_path)
        record_nudge_shown(trw_dir, "L-wire-002", "VALIDATE", turn=7, surface_type="phase_transition")

        events = _read_events_jsonl(trw_dir / "context" / "session-events.jsonl")
        assert events[0]["data"]["surface_type"] == "phase_transition"

    def test_multiple_nudges_append(self, tmp_path: Path) -> None:
        """Repeated record_nudge_shown calls append, do not overwrite."""
        trw_dir = _setup_trw_dir(tmp_path)
        for i in range(3):
            record_nudge_shown(trw_dir, f"L-{i:03d}", "IMPLEMENT", turn=i)

        events = _read_events_jsonl(trw_dir / "context" / "session-events.jsonl")
        assert len(events) == 3
        learning_ids = [e["learning_id"] for e in events]
        assert learning_ids == ["L-000", "L-001", "L-002"]

    def test_ceremony_state_still_updated(self, tmp_path: Path) -> None:
        """The existing ceremony-state.json update is preserved (no regression)."""
        from trw_mcp.state._nudge_state import read_ceremony_state

        trw_dir = _setup_trw_dir(tmp_path)
        record_nudge_shown(trw_dir, "L-regress", "DELIVER", turn=9)

        cs = read_ceremony_state(trw_dir)
        assert "L-regress" in cs.nudge_history
        assert "DELIVER" in cs.nudge_history["L-regress"]["phases_shown"]
        assert cs.nudge_history["L-regress"]["last_shown_turn"] == 9


# ===========================================================================
# FR05: Deliver event includes nudge_summary
# ===========================================================================


class TestDeliverNudgeSummary:
    """FR05: trw_deliver_complete event includes nudge_summary."""

    def test_deliver_event_has_nudge_summary(self, tmp_path: Path) -> None:
        """When CeremonyState has nudge_counts, deliver event includes nudge_summary."""
        trw_dir = _setup_trw_dir(tmp_path)

        # Write ceremony state with nudge_counts
        state = CeremonyState(
            session_started=True,
            deliver_called=True,
            nudge_counts={"session_start": 3, "checkpoint": 2, "deliver": 1},
        )
        write_ceremony_state(trw_dir, state)

        # Create a mock run directory with meta/events.jsonl
        run_dir = trw_dir / "runs" / "test-run"
        (run_dir / "meta").mkdir(parents=True)

        # Import and call the event logger directly to verify the nudge_summary
        # is included. We simulate what ceremony.py does.
        from trw_mcp.state._nudge_state import read_ceremony_state

        cs = read_ceremony_state(trw_dir)
        nudge_summary = dict(cs.nudge_counts)

        from trw_mcp.state.persistence import FileEventLogger, FileStateWriter

        writer = FileStateWriter()
        event_logger = FileEventLogger(writer)
        event_logger.log_event(
            run_dir / "meta" / "events.jsonl",
            "trw_deliver_complete",
            {
                "critical_steps_completed": 2,
                "nudge_summary": nudge_summary,
            },
        )

        events = _read_events_jsonl(run_dir / "meta" / "events.jsonl")
        assert len(events) == 1
        evt = events[0]
        assert evt["event"] == "trw_deliver_complete"
        assert "nudge_summary" in evt
        ns = evt["nudge_summary"]
        assert ns["session_start"] == 3
        assert ns["checkpoint"] == 2
        assert ns["deliver"] == 1

    def test_deliver_event_empty_nudge_summary(self, tmp_path: Path) -> None:
        """When CeremonyState has empty nudge_counts, nudge_summary is {}."""
        trw_dir = _setup_trw_dir(tmp_path)
        state = CeremonyState(session_started=True)
        write_ceremony_state(trw_dir, state)

        from trw_mcp.state._nudge_state import read_ceremony_state

        cs = read_ceremony_state(trw_dir)
        nudge_summary = dict(cs.nudge_counts)
        assert nudge_summary == {}

    def test_nudge_summary_read_from_ceremony_state(self, tmp_path: Path) -> None:
        """nudge_counts round-trips correctly through CeremonyState."""
        trw_dir = _setup_trw_dir(tmp_path)
        state = CeremonyState(
            nudge_counts={"session_start": 5, "build_check": 2},
        )
        write_ceremony_state(trw_dir, state)

        from trw_mcp.state._nudge_state import read_ceremony_state

        loaded = read_ceremony_state(trw_dir)
        assert loaded.nudge_counts == {"session_start": 5, "build_check": 2}


# ===========================================================================
# PRD-CORE-146 W2B FR06/FR07: structlog nudge_shown + nudge_skipped
# ===========================================================================


class TestStructlogNudgeTelemetry:
    """FR06: structlog INFO nudge_shown; FR07: structlog DEBUG nudge_skipped."""

    def test_nudge_shown_info_emitted_all_paths(self, tmp_path: Path) -> None:
        """FR06: learning_injection messenger emits nudge_shown INFO with required fields.

        Drives the learning_injection messenger path end-to-end through
        append_ceremony_status and asserts the structlog INFO event is
        captured with pool/messenger/learning_id/phase/client_id/turn.
        """
        import structlog

        from trw_mcp.state._nudge_state import write_ceremony_state
        from trw_mcp.tools._ceremony_status import append_ceremony_status

        trw_dir = _setup_trw_dir(tmp_path)

        # Pre-populate a ceremony state that forces the learning_injection
        # messenger to fire on a real learning.
        state = CeremonyState(session_started=True, phase="implement")
        write_ceremony_state(trw_dir, state)

        # Write workspace config selecting learning_injection messenger.
        (trw_dir / "config.yaml").write_text(
            "nudge_messenger: learning_injection\nnudge_enabled: true\n",
            encoding="utf-8",
        )

        fake_recall_context = type("Ctx", (), {"modified_files": ["src/foo.py"], "inferred_domains": set()})()
        fake_learning = {
            "id": "L-fr06-a",
            "summary": "test learning summary for fr06",
            "nudge_line": "fr06 nudge line",
            "impact": 0.8,
        }

        import trw_mcp.state.ceremony_nudge as cn

        monkey_build = cn.build_recall_context if hasattr(cn, "build_recall_context") else None
        monkey_recall = cn.recall_learnings if hasattr(cn, "recall_learnings") else None

        # Use direct attribute replacement (restored manually in finally).
        from trw_mcp.state import memory_adapter
        from trw_mcp.state import recall_context as _recall_ctx_mod
        from trw_mcp.tools import _recall_impl

        orig_build = _recall_impl.build_recall_context
        orig_build_state = _recall_ctx_mod.build_recall_context
        orig_recall = memory_adapter.recall_learnings

        _recall_impl.build_recall_context = lambda *a, **kw: fake_recall_context  # type: ignore[assignment]
        _recall_ctx_mod.build_recall_context = lambda *a, **kw: fake_recall_context  # type: ignore[assignment]
        memory_adapter.recall_learnings = lambda *a, **kw: [fake_learning]  # type: ignore[assignment]
        try:
            with structlog.testing.capture_logs() as captured:
                append_ceremony_status({}, trw_dir=trw_dir)
        finally:
            _recall_impl.build_recall_context = orig_build  # type: ignore[assignment]
            _recall_ctx_mod.build_recall_context = orig_build_state  # type: ignore[assignment]
            memory_adapter.recall_learnings = orig_recall  # type: ignore[assignment]
            _ = monkey_build, monkey_recall  # silence ruff

        shown = [e for e in captured if e.get("event") == "nudge_shown"]
        assert shown, f"no nudge_shown INFO captured; events={[e.get('event') for e in captured]}"
        evt = shown[0]
        for field_name in ("pool", "messenger", "learning_id", "phase", "client_id", "turn"):
            assert field_name in evt, f"missing field {field_name} in {evt}"
        assert evt["log_level"] == "info"
        assert evt["learning_id"] == "L-fr06-a"

    def test_nudge_shown_info_preserves_jsonl_event(self, tmp_path: Path) -> None:
        """NFR03: the legacy JSONL nudge_shown event is still emitted.

        record_nudge_shown writes to session-events.jsonl — that contract is
        additive-only; the new structlog INFO must not replace it.
        """
        trw_dir = _setup_trw_dir(tmp_path)
        record_nudge_shown(trw_dir, "L-additive-check", "implement", turn=5)

        events_path = trw_dir / "context" / "session-events.jsonl"
        events = _read_events_jsonl(events_path)
        assert any(e.get("event") == "nudge_shown" for e in events), (
            f"legacy JSONL nudge_shown missing; events={events}"
        )

    def test_nudge_skipped_pool_cooldown_reason(self, tmp_path: Path) -> None:
        """FR07: pool_cooldown skip site emits nudge_skipped DEBUG event."""
        import structlog

        from trw_mcp.models.config._client_profile import NudgePoolWeights
        from trw_mcp.state._nudge_rules import _select_nudge_pool

        _ = tmp_path  # unused — pure in-memory test

        state = CeremonyState(
            session_started=True,
            phase="implement",
            tool_call_counter=1,
            pool_cooldown_until={"workflow": 100, "learnings": 100, "ceremony": 100, "context": 100},
        )
        weights = NudgePoolWeights(workflow=25, learnings=25, ceremony=25, context=25)

        with structlog.testing.capture_logs() as captured:
            result = _select_nudge_pool(state, weights)

        assert result is None  # all pools cooled down
        skipped = [e for e in captured if e.get("event") == "nudge_skipped"]
        assert skipped, f"no nudge_skipped captured; events={[e.get('event') for e in captured]}"
        reasons = {e.get("reason") for e in skipped}
        assert "pool_cooldown" in reasons
        sample = next(e for e in skipped if e.get("reason") == "pool_cooldown")
        assert sample["log_level"] == "debug"
        for field_name in ("reason", "pool", "learning_id", "client_id"):
            assert field_name in sample

    def test_nudge_skipped_phase_dedup_reason(self, tmp_path: Path) -> None:
        """FR07: phase_dedup skip path emits nudge_skipped DEBUG event."""
        import structlog

        from trw_mcp.state._ceremony_progress_state import NudgeHistoryEntry
        from trw_mcp.state.ceremony_nudge import _select_learning_injection_candidate

        trw_dir = _setup_trw_dir(tmp_path)

        state = CeremonyState(
            session_started=True,
            phase="validate",
            nudge_history={
                "L-dedup-x": NudgeHistoryEntry(
                    phases_shown=["validate"],
                    turn_first_shown=1,
                    last_shown_turn=1,
                ),
            },
        )

        fake_recall_context = type("Ctx", (), {"modified_files": ["src/foo.py"], "inferred_domains": set()})()
        fake_learning = {
            "id": "L-dedup-x",
            "summary": "already shown in validate phase",
            "nudge_line": "skip me",
        }

        from trw_mcp.state import memory_adapter
        from trw_mcp.state import recall_context as _recall_ctx_mod
        from trw_mcp.tools import _recall_impl

        orig_build = _recall_impl.build_recall_context
        orig_build_state = _recall_ctx_mod.build_recall_context
        orig_recall = memory_adapter.recall_learnings
        _recall_impl.build_recall_context = lambda *a, **kw: fake_recall_context  # type: ignore[assignment]
        _recall_ctx_mod.build_recall_context = lambda *a, **kw: fake_recall_context  # type: ignore[assignment]
        memory_adapter.recall_learnings = lambda *a, **kw: [fake_learning]  # type: ignore[assignment]
        try:
            with structlog.testing.capture_logs() as captured:
                selected, _ = _select_learning_injection_candidate(state, trw_dir, skip_phase_duplicates=True)
        finally:
            _recall_impl.build_recall_context = orig_build  # type: ignore[assignment]
            _recall_ctx_mod.build_recall_context = orig_build_state  # type: ignore[assignment]
            memory_adapter.recall_learnings = orig_recall  # type: ignore[assignment]

        assert selected is None, "phase-shown learning must be filtered out"
        skipped = [e for e in captured if e.get("event") == "nudge_skipped" and e.get("reason") == "phase_dedup"]
        assert skipped, f"no phase_dedup nudge_skipped captured; events={[e.get('event') for e in captured]}"
        evt = skipped[0]
        assert evt["log_level"] == "debug"
        assert evt["learning_id"] == "L-dedup-x"
        for field_name in ("reason", "pool", "learning_id", "client_id"):
            assert field_name in evt
