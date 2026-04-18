"""HPOTelemetryEvent — unified event base class for HPO measurement substrate.

PRD-HPO-MEAS-001 §7 (Naming Resolution) + §5 FR-3/FR-6.

This module introduces ``HPOTelemetryEvent`` (Pydantic v2, strict, frozen,
extra=forbid) as the single base class for all new trw-mcp telemetry
emitters. It intentionally coexists in parallel with the legacy
``trw_mcp.telemetry.models.TelemetryEvent`` (4-field installation-scoped
event) during Phase-1 parallel-emit — the legacy class stays untouched.

Subclasses (12) set only ``event_type`` and a default ``emitter`` so the
base schema remains uniform across surfaces.

FR-6: ``parent_event_id`` is nullable, string-typed, and references another
``event_id`` within the same ``run_id``. Validation is advisory — dangling
references produce a WARN log and a returned list, never an exception.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

_log = structlog.get_logger(__name__)


def _evt_id() -> str:
    """Return a new opaque event id with the ``evt_`` prefix."""
    return f"evt_{uuid4().hex}"


def _utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(tz=timezone.utc)


class HPOTelemetryEvent(BaseModel):
    """Unified HPO telemetry event base (PRD-HPO-MEAS-001 FR-3).

    All new telemetry emitters subclass this. Subclasses should only set
    class-level defaults for ``event_type`` and ``emitter``; they must not
    add new fields — payload extensions belong in ``payload``.

    ``surface_snapshot_id`` is allowed to be the empty string during Phase 1
    (PRD §9); FR-2 will enforce non-empty values once the artifact registry
    lands in Phase 2.

    ``parent_event_id`` (FR-6) enables DAG-ready causal chains for H3
    investigations; validation is advisory only.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    event_id: str = Field(default_factory=_evt_id)
    session_id: str
    run_id: str | None = None
    ts: datetime = Field(default_factory=_utc_now)
    emitter: str
    event_type: str
    surface_snapshot_id: str = ""
    parent_event_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class CeremonyEvent(HPOTelemetryEvent):
    """Ceremony phase-gate + compliance events."""

    event_type: str = "ceremony"
    emitter: str = "ceremony"


class ContractEvent(HPOTelemetryEvent):
    """Agent-contract validation events."""

    event_type: str = "contract"
    emitter: str = "contract"


class PhaseExposureEvent(HPOTelemetryEvent):
    """Phase-exposure telemetry for H1 sequencing signal."""

    event_type: str = "phase_exposure"
    emitter: str = "phase_exposure"


class ObserverEvent(HPOTelemetryEvent):
    """Observer-layer emissions (oversight hooks)."""

    event_type: str = "observer"
    emitter: str = "observer"


class MCPSecurityEvent(HPOTelemetryEvent):
    """MCP capability-scope + registry security events."""

    event_type: str = "mcp_security"
    emitter: str = "mcp_security"


class MetaTuneEvent(HPOTelemetryEvent):
    """Meta-tune hyperparameter-exposure events."""

    event_type: str = "meta_tune"
    emitter: str = "meta_tune"


class ThrashingEvent(HPOTelemetryEvent):
    """Detected thrashing / retry-churn events."""

    event_type: str = "thrashing"
    emitter: str = "thrashing"


class LLMCallEvent(HPOTelemetryEvent):
    """LLM invocation telemetry (per-call cost/tokens/timing in payload)."""

    event_type: str = "llm_call"
    emitter: str = "llm"


class ToolCallEvent(HPOTelemetryEvent):
    """MCP tool-call timing events."""

    event_type: str = "tool_call"
    emitter: str = "tool_call_timing"


class SessionStartEvent(HPOTelemetryEvent):
    """Emitted at ``trw_session_start``."""

    event_type: str = "session_start"
    emitter: str = "session"


class SessionEndEvent(HPOTelemetryEvent):
    """Emitted at ``trw_deliver`` (session close)."""

    event_type: str = "session_end"
    emitter: str = "session"


class CeremonyComplianceEvent(HPOTelemetryEvent):
    """Ceremony-compliance scoring events (per-run rollup)."""

    event_type: str = "ceremony_compliance"
    emitter: str = "ceremony"


def validate_parent_within_run(
    events: list[HPOTelemetryEvent],
    *,
    run_id: str,
) -> list[str]:
    """Return event_ids whose ``parent_event_id`` does not resolve within ``run_id``.

    FR-6 is advisory: this function NEVER raises. Dangling parent refs are
    logged at WARN level and returned as a list so callers can surface the
    issue without rejecting ingestion.

    Args:
        events: Events to inspect (any subclass of HPOTelemetryEvent).
        run_id: Run scope; only events with matching ``run_id`` are checked,
            and parent resolution is restricted to that same run.

    Returns:
        Sorted list of event_ids with dangling parent references. Empty list
        when all parent_event_ids resolve (or are null).
    """
    in_run = [e for e in events if e.run_id == run_id]
    known_ids = {e.event_id for e in in_run}
    dangling: list[str] = []
    for ev in in_run:
        parent = ev.parent_event_id
        if parent is None:
            continue
        if parent not in known_ids:
            dangling.append(ev.event_id)
            _log.warning(
                "hpo_telemetry_parent_unresolved",
                run_id=run_id,
                event_id=ev.event_id,
                parent_event_id=parent,
                event_type=ev.event_type,
            )
    return sorted(dangling)


__all__ = [
    "HPOTelemetryEvent",
    "CeremonyEvent",
    "ContractEvent",
    "PhaseExposureEvent",
    "ObserverEvent",
    "MCPSecurityEvent",
    "MetaTuneEvent",
    "ThrashingEvent",
    "LLMCallEvent",
    "ToolCallEvent",
    "SessionStartEvent",
    "SessionEndEvent",
    "CeremonyComplianceEvent",
    "validate_parent_within_run",
]
