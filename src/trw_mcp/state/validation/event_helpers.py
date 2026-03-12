"""Event I/O helpers for phase gate validation.

Shared helpers for reading events.jsonl and checking for recognized
event types (reflection, CLAUDE.md sync, validate-pass).
Used by both validation modules and _phase_validators.py.
"""

from __future__ import annotations

from pathlib import Path

# Recognized event names for reflection and CLAUDE.md sync checks.
_REFLECTION_EVENTS: frozenset[str] = frozenset(
    {"reflection_complete", "trw_reflect_complete"}
)
_SYNC_EVENTS: frozenset[str] = frozenset({"claude_md_sync", "claude_md_synced"})


def _read_events(events_path: Path) -> list[dict[str, object]]:
    """Read events.jsonl via FileStateReader (lazy import to avoid circular deps).

    Args:
        events_path: Path to events.jsonl file.

    Returns:
        List of event dicts, or empty list if file does not exist.
    """
    if not events_path.exists():
        return []
    from trw_mcp.state.persistence import FileStateReader

    return FileStateReader().read_jsonl(events_path)


def _events_contain(
    events: list[dict[str, object]],
    event_names: frozenset[str],
) -> bool:
    """Check whether any event matches one of the given event names.

    Args:
        events: List of event dicts from events.jsonl.
        event_names: Set of event type strings to match.

    Returns:
        True if at least one event matches.
    """
    return any(e.get("event") in event_names for e in events)


def _is_validate_pass(event: dict[str, object]) -> bool:
    """Check if an event represents a passing validate phase gate.

    Args:
        event: Single event dict from events.jsonl.

    Returns:
        True if the event is a phase_check for validate with valid=True.
    """
    if event.get("event") != "phase_check":
        return False
    data = event.get("data")
    if not isinstance(data, dict):
        return False
    return data.get("phase") == "validate" and data.get("valid") is True


def _event_has_tool(event: dict[str, object], tool_name: str) -> bool:
    """Check if an event has a specific tool invocation.

    Args:
        event: Single event dict from events.jsonl.
        tool_name: Tool name to match.

    Returns:
        True if the event references the given tool.
    """
    if event.get("event") == "tool_invocation" and event.get("tool_name") == tool_name:
        return True
    data = event.get("data")
    return isinstance(data, dict) and data.get("tool_name") == tool_name


def _event_has_score(event: dict[str, object], key: str) -> bool:
    """Check if an event data dict contains a numeric score for a key.

    Args:
        event: Single event dict from events.jsonl.
        key: Key to look for in event data.

    Returns:
        True if the key exists with a numeric value.
    """
    data = event.get("data")
    if not isinstance(data, dict):
        return False
    val = data.get(key)
    return isinstance(val, (int, float))
