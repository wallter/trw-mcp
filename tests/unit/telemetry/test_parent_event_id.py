"""Tests for parent_event_id + validate_parent_within_run — FR-6."""

from __future__ import annotations

from tests._structlog_capture import captured_structlog  # noqa: F401
from trw_mcp.telemetry.event_base import (
    CeremonyEvent,
    HPOTelemetryEvent,
    LLMCallEvent,
    ToolCallEvent,
    validate_parent_within_run,
)


def test_parent_event_id_nullable() -> None:
    ev = HPOTelemetryEvent(session_id="s1", emitter="x", event_type="x")
    assert ev.parent_event_id is None


def test_parent_event_id_accepts_str() -> None:
    ev = HPOTelemetryEvent(
        session_id="s1",
        emitter="x",
        event_type="x",
        parent_event_id="evt_abc123",
    )
    assert ev.parent_event_id == "evt_abc123"


def test_validate_parent_within_run_returns_empty_for_resolved_chain() -> None:
    root = CeremonyEvent(session_id="s1", run_id="r1")
    child = LLMCallEvent(session_id="s1", run_id="r1", parent_event_id=root.event_id)
    grand = ToolCallEvent(session_id="s1", run_id="r1", parent_event_id=child.event_id)
    assert validate_parent_within_run([root, child, grand], run_id="r1") == []


def test_validate_parent_within_run_returns_dangling_refs() -> None:
    root = CeremonyEvent(session_id="s1", run_id="r1")
    orphan = LLMCallEvent(session_id="s1", run_id="r1", parent_event_id="evt_does_not_exist")
    result = validate_parent_within_run([root, orphan], run_id="r1")
    assert result == [orphan.event_id]


def test_validate_parent_within_run_null_parents_are_valid() -> None:
    a = CeremonyEvent(session_id="s1", run_id="r1")
    b = LLMCallEvent(session_id="s1", run_id="r1")
    assert validate_parent_within_run([a, b], run_id="r1") == []


def test_validate_parent_within_run_ignores_other_runs() -> None:
    # A parent that lives in a different run should NOT satisfy the lookup
    foreign = CeremonyEvent(session_id="s1", run_id="r2")
    ev = LLMCallEvent(session_id="s1", run_id="r1", parent_event_id=foreign.event_id)
    result = validate_parent_within_run([foreign, ev], run_id="r1")
    assert result == [ev.event_id]


def test_validate_parent_within_run_logs_warning_not_rejects(
    captured_structlog: list[dict[str, object]],
) -> None:
    orphan = LLMCallEvent(session_id="s1", run_id="r1", parent_event_id="evt_missing")
    # Should not raise
    dangling = validate_parent_within_run([orphan], run_id="r1")
    assert dangling == [orphan.event_id]
    warns = [
        e
        for e in captured_structlog
        if e.get("event") == "hpo_telemetry_parent_unresolved" and e.get("log_level") == "warning"
    ]
    assert len(warns) == 1
    assert warns[0].get("parent_event_id") == "evt_missing"
    assert warns[0].get("run_id") == "r1"


def test_validate_parent_within_run_multiple_dangling_sorted() -> None:
    a = LLMCallEvent(session_id="s1", run_id="r1", parent_event_id="evt_zzz")
    b = LLMCallEvent(session_id="s1", run_id="r1", parent_event_id="evt_yyy")
    result = validate_parent_within_run([a, b], run_id="r1")
    assert result == sorted([a.event_id, b.event_id])
