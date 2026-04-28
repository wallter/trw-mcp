"""Shared tool trace context fields for legacy and unified telemetry."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Mapping, MutableMapping
from typing import Literal
from uuid import uuid4

from typing_extensions import TypedDict

CausalRelation = Literal["root", "nested", "spawned", "caused_by"]
_TRACE_COUNTER = 0
_TRACE_COUNTER_LOCK = threading.Lock()
_HASH_PREFIX_LEN = 16


class ToolTraceFields(TypedDict):
    """Canonical trace fields shared by legacy and unified tool telemetry."""

    event_id: str
    parent_event_id: str | None
    tool_call_id: str
    turn_index: int
    input_hash: str
    output_hash: str
    task_profile_hash: str
    causal_relation: CausalRelation


class TaskProfileTraceField(TypedDict):
    """Trace metadata available even when a caller has no full trace context."""

    task_profile_hash: str


def new_trace_event_id() -> str:
    """Return an opaque event ID compatible with unified telemetry events."""
    return f"evt_{uuid4().hex}"


def next_turn_index() -> int:
    """Return a process-local monotonic tool turn index."""
    global _TRACE_COUNTER
    with _TRACE_COUNTER_LOCK:
        _TRACE_COUNTER += 1
        return _TRACE_COUNTER


def stable_payload_hash(value: object) -> str:
    """Return a short stable hash without exposing raw input or output data."""
    try:
        payload = json.dumps(value, sort_keys=True, default=repr, separators=(",", ":"))
    except (TypeError, ValueError):
        payload = repr(value)
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:_HASH_PREFIX_LEN]


def _resolve_causal_relation(parent_event_id: str | None, causal_relation: CausalRelation | None) -> CausalRelation:
    if causal_relation is not None:
        return causal_relation
    return "nested" if parent_event_id else "root"


def build_tool_trace_fields(
    *,
    tool_name: str,
    event_id: str | None = None,
    parent_event_id: str | None = None,
    tool_call_id: str | None = None,
    input_data: object | None = None,
    output_data: object | None = None,
    task_profile_hash: str = "",
    causal_relation: CausalRelation | None = None,
) -> ToolTraceFields:
    """Build the canonical cross-surface trace field set for a tool call."""
    return {
        "event_id": event_id or new_trace_event_id(),
        "parent_event_id": parent_event_id,
        "tool_call_id": tool_call_id or "",
        "turn_index": next_turn_index(),
        "input_hash": stable_payload_hash({"tool": tool_name, "input": input_data}),
        "output_hash": stable_payload_hash({"tool": tool_name, "output": output_data}),
        "task_profile_hash": task_profile_hash,
        "causal_relation": _resolve_causal_relation(parent_event_id, causal_relation),
    }


def with_task_profile_hash(fields: ToolTraceFields, task_profile_hash: str) -> ToolTraceFields:
    """Return trace fields with run task-profile hash filled when absent."""
    if not task_profile_hash or fields["task_profile_hash"]:
        return fields
    return {**fields, "task_profile_hash": task_profile_hash}


def task_profile_trace_field(task_profile_hash: str) -> TaskProfileTraceField | None:
    """Return a minimal task-profile trace field when a full trace is unavailable."""
    if not task_profile_hash:
        return None
    return {"task_profile_hash": task_profile_hash}


def merge_trace_fields(target: MutableMapping[str, object], trace_fields: Mapping[str, object] | None) -> None:
    """Merge trace fields into an event dict, preserving explicit target values."""
    if not trace_fields:
        return
    for key, value in trace_fields.items():
        target.setdefault(key, value)
