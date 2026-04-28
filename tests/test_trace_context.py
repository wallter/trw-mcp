"""Tests for shared tool trace-context helpers."""

from __future__ import annotations

from trw_mcp.telemetry.trace_context import build_tool_trace_fields, stable_payload_hash, with_task_profile_hash


def test_trace_hashes_are_deterministic_and_redacted() -> None:
    payload = {"secret": "do-not-log", "value": 42}
    first = build_tool_trace_fields(tool_name="tool", input_data=payload, output_data={"ok": True})
    second = build_tool_trace_fields(tool_name="tool", input_data=payload, output_data={"ok": True})

    assert first["input_hash"] == second["input_hash"]
    assert first["output_hash"] == second["output_hash"]
    assert "do-not-log" not in str(first)


def test_sequential_root_calls_have_no_parent_and_increasing_turns() -> None:
    first = build_tool_trace_fields(tool_name="first")
    second = build_tool_trace_fields(tool_name="second")

    assert first["parent_event_id"] is None
    assert second["parent_event_id"] is None
    assert first["causal_relation"] == "root"
    assert second["causal_relation"] == "root"
    assert second["turn_index"] > first["turn_index"]


def test_nested_parent_sets_nested_relation() -> None:
    parent = build_tool_trace_fields(tool_name="parent")
    child = build_tool_trace_fields(tool_name="child", parent_event_id=parent["event_id"])

    assert child["parent_event_id"] == parent["event_id"]
    assert child["causal_relation"] == "nested"


def test_with_task_profile_hash_preserves_existing_hash() -> None:
    fields = build_tool_trace_fields(tool_name="tool", task_profile_hash="explicit")

    assert with_task_profile_hash(fields, "run-hash")["task_profile_hash"] == "explicit"


def test_stable_payload_hash_handles_unjsonable_values() -> None:
    class Unjsonable:
        pass

    assert stable_payload_hash(Unjsonable())
