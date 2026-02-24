"""Telemetry event models — PRD-CORE-031.

Pydantic v2 models for anonymized, opt-in telemetry events.
These models are serialized to local JSONL before any optional
remote transmission.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


def _utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(tz=timezone.utc)


class TelemetryEvent(BaseModel):
    """Base telemetry event.

    All telemetry events share these fields. ``installation_id`` is an
    anonymized identifier — no PII is stored here.
    """

    model_config = ConfigDict(use_enum_values=True)

    timestamp: datetime = Field(default_factory=_utc_now)
    installation_id: str
    framework_version: str
    event_type: str


class SessionStartEvent(TelemetryEvent):
    """Emitted when a TRW session starts (trw_session_start tool called)."""

    event_type: str = "session_start"
    run_id: str | None = None
    learnings_loaded: int = 0


class ToolInvocationEvent(TelemetryEvent):
    """Emitted after each MCP tool call completes."""

    event_type: str = "tool_invocation"
    tool_name: str
    duration_ms: int = 0
    success: bool = True
    phase: str = ""


class CeremonyComplianceEvent(TelemetryEvent):
    """Emitted when ceremony compliance is evaluated for a run."""

    event_type: str = "ceremony_compliance"
    run_id: str
    score: int = 0
    phases_completed: list[str] = Field(default_factory=list)


class SessionEndEvent(TelemetryEvent):
    """Emitted when a TRW session ends (trw_deliver tool called)."""

    event_type: str = "session_end"
    total_duration_ms: int = 0
    tools_invoked: int = 0
    ceremony_score: int = 0
