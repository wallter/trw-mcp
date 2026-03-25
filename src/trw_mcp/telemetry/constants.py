"""Telemetry contract — single source of truth for event types, fields, phases, and status values.

Canonical source of telemetry constants for trw-mcp.
Originally shared with backend via trw-shared; inlined for standalone publication.
Grafana dashboard SQL queries reference these same string values.
"""

from __future__ import annotations


class EventType:
    """Event type strings emitted by trw-mcp and stored in telemetry_events.event_type."""

    TOOL_INVOCATION = "tool_invocation"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    CEREMONY_COMPLIANCE = "ceremony_compliance"

    ALL: list[str] = [TOOL_INVOCATION, SESSION_START, SESSION_END, CEREMONY_COMPLIANCE]


class Phase:
    """TRW framework execution phases stored in telemetry_events.phase.

    Values are lowercase to match the canonical Phase enum in trw-mcp models/run.py.
    """

    RESEARCH = "research"
    PLAN = "plan"
    IMPLEMENT = "implement"
    VALIDATE = "validate"
    REVIEW = "review"
    DELIVER = "deliver"
    UNKNOWN = "unknown"

    ALL: list[str] = [RESEARCH, PLAN, IMPLEMENT, VALIDATE, REVIEW, DELIVER]


class Status:
    """Status values for tool invocations stored in telemetry_events.status."""

    SUCCESS = "success"
    ERROR = "error"


# Fields that map directly to telemetry_events DB columns.
# Anything not in this set goes into the payload JSON column.
MAPPED_FIELDS: frozenset[str] = frozenset(
    {
        "installation_id",
        "event_type",
        "session_id",
        "framework_version",
        "python_version",
        "os_platform",
        "tool_name",
        "duration_ms",
        "status",
        "error_type",
        "phase",
        "ceremony_score",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "pytest_passed",
        "test_count",
        "coverage_pct",
        "mypy_passed",
        "attributes",
    }
)
