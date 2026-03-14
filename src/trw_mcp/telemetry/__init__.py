"""Telemetry subsystem — PRD-CORE-031.

Opt-in, anonymized telemetry for TRW framework usage.
"""

from __future__ import annotations

from trw_mcp.telemetry.anonymizer import anonymize_installation_id, redact_paths, strip_pii
from trw_mcp.telemetry.client import TelemetryClient
from trw_mcp.telemetry.models import (
    CeremonyComplianceEvent,
    SessionEndEvent,
    SessionStartEvent,
    TelemetryEvent,
    ToolInvocationEvent,
)
from trw_mcp.telemetry.publisher import publish_learnings
from trw_mcp.telemetry.remote_recall import fetch_shared_learnings
from trw_mcp.telemetry.pipeline import TelemetryPipeline
from trw_mcp.telemetry.sender import BatchSender

__all__ = [
    "BatchSender",
    "CeremonyComplianceEvent",
    "TelemetryPipeline",
    "SessionEndEvent",
    "SessionStartEvent",
    "TelemetryClient",
    "TelemetryEvent",
    "ToolInvocationEvent",
    "anonymize_installation_id",
    "fetch_shared_learnings",
    "publish_learnings",
    "redact_paths",
    "strip_pii",
]
