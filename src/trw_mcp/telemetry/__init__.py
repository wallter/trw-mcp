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
from trw_mcp.telemetry.boot_audit import (
    ResolutionFailure,
    check_defaults,
    run_boot_audit,
)
from trw_mcp.telemetry.client import TelemetryClient
from trw_mcp.telemetry.event_base import (
    EVENT_PAYLOAD_KEY_REGISTRY,
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
    SurfaceRegistered,
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
from trw_mcp.telemetry.tool_call_timing import (
    build_tool_call_event,
    clear_pricing_cache,
    wrap_tool,
)
from trw_mcp.telemetry.unified_events import (
    UnifiedEventWriter,
    get_default_writer,
    resolve_unified_events_path,
)
from trw_mcp.telemetry.unified_events import (
    emit as emit_unified,
)

__all__ = [
    "EVENT_PAYLOAD_KEY_REGISTRY",
    "EVENT_TYPE_REGISTRY",
    # HPO-MEAS-001 run snapshot
    "MANIFEST_FILENAME",
    # CORE-031 legacy
    "BatchSender",
    "CeremonyComplianceEvent",
    # HPO-MEAS-001 unified schema
    "CeremonyEvent",
    # HPO-MEAS-001 surface identity
    "ComponentFingerprint",
    "ContractEvent",
    "DefaultResolutionError",
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
    # HPO-MEAS-001 NFR-12 boot audit
    "ResolutionFailure",
    "SessionEndEvent",
    "SessionStartEvent",
    "SurfaceArtifact",
    "SurfaceRegistered",
    "SurfaceRegistry",
    "SurfaceSnapshot",
    "TelemetryClient",
    "TelemetryEvent",
    "TelemetryPipeline",
    "ThrashingEvent",
    "ToolCallEvent",
    "ToolInvocationEvent",
    # HPO-MEAS-001 unified writer + FR-4 timing middleware
    "UnifiedEventWriter",
    "anonymize_installation_id",
    "build_tool_call_event",
    "check_defaults",
    "clear_pricing_cache",
    "clear_snapshot_cache",
    "emit_h1_observe_mode_warning",
    "emit_unified",
    "fetch_shared_learnings",
    "get_default_writer",
    "load_manifest",
    "publish_learnings",
    "redact_paths",
    "resolve_surface_registry",
    "resolve_surface_snapshot",
    "resolve_unified_events_path",
    "run_boot_audit",
    "snapshot_to_yaml",
    "stamp_session",
    "strip_pii",
    "validate_parent_within_run",
    "wrap_tool",
    "write_manifest",
    "yaml_to_snapshot",
]
