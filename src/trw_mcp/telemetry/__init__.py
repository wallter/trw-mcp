"""Telemetry subsystem — PRD-CORE-031 + PRD-HPO-MEAS-001.

Opt-in, anonymized telemetry (CORE-031) + unified HPOTelemetryEvent schema
and hash-pinned surface registry (HPO-MEAS-001). The two schemas coexist
during Phase 1; see ``CLAUDE.md`` in this directory for editing rules.
"""

from __future__ import annotations

from trw_mcp.telemetry.anonymizer import anonymize_installation_id, redact_paths, strip_pii
from trw_mcp.telemetry.artifact_registry import (
    ComponentFingerprint,
    SurfaceArtifact,
    SurfaceRegistry,
    SurfaceSnapshot,
    clear_snapshot_cache,
    resolve_surface_registry,
    resolve_surface_snapshot,
)
from trw_mcp.telemetry.client import TelemetryClient
from trw_mcp.telemetry.event_base import (
    EVENT_TYPE_REGISTRY,
    CeremonyEvent,
    ContractEvent,
    DefaultResolutionError,
    H1ObserveModeWarning,
    HPOCeremonyComplianceEvent,
    HPOSessionEndEvent,
    HPOSessionStartEvent,
    HPOTelemetryEvent,
    LLMCallEvent,
    MCPSecurityEvent,
    MetaTuneEvent,
    ObserverEvent,
    PhaseExposureEvent,
    ThrashingEvent,
    ToolCallEvent,
    emit_h1_observe_mode_warning,
    validate_parent_within_run,
)
from trw_mcp.telemetry.models import (
    CeremonyComplianceEvent,
    SessionEndEvent,
    SessionStartEvent,
    TelemetryEvent,
    ToolInvocationEvent,
)
from trw_mcp.telemetry.pipeline import TelemetryPipeline
from trw_mcp.telemetry.publisher import publish_learnings
from trw_mcp.telemetry.remote_recall import fetch_shared_learnings
from trw_mcp.telemetry.sender import BatchSender
from trw_mcp.telemetry.surface_manifest import (
    MANIFEST_FILENAME,
    load_manifest,
    snapshot_to_yaml,
    stamp_session,
    write_manifest,
    yaml_to_snapshot,
)

__all__ = [
    # CORE-031 legacy
    "BatchSender",
    "CeremonyComplianceEvent",
    "SessionEndEvent",
    "SessionStartEvent",
    "TelemetryClient",
    "TelemetryEvent",
    "TelemetryPipeline",
    "ToolInvocationEvent",
    "anonymize_installation_id",
    "fetch_shared_learnings",
    "publish_learnings",
    "redact_paths",
    "strip_pii",
    # HPO-MEAS-001 unified schema
    "CeremonyEvent",
    "ContractEvent",
    "DefaultResolutionError",
    "EVENT_TYPE_REGISTRY",
    "H1ObserveModeWarning",
    "HPOCeremonyComplianceEvent",
    "HPOSessionEndEvent",
    "HPOSessionStartEvent",
    "HPOTelemetryEvent",
    "LLMCallEvent",
    "MCPSecurityEvent",
    "MetaTuneEvent",
    "ObserverEvent",
    "PhaseExposureEvent",
    "ThrashingEvent",
    "ToolCallEvent",
    "emit_h1_observe_mode_warning",
    "validate_parent_within_run",
    # HPO-MEAS-001 surface identity
    "ComponentFingerprint",
    "SurfaceArtifact",
    "SurfaceRegistry",
    "SurfaceSnapshot",
    "clear_snapshot_cache",
    "resolve_surface_registry",
    "resolve_surface_snapshot",
    # HPO-MEAS-001 run snapshot
    "MANIFEST_FILENAME",
    "load_manifest",
    "snapshot_to_yaml",
    "stamp_session",
    "write_manifest",
    "yaml_to_snapshot",
]
