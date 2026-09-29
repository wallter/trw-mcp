"""Channel manifest substrate for trw-distill client integration.

Phase A + Phase B + Phase C + Phase D1 exports — ChannelEntry schema,
locking, provenance, manifest loader, conflict detection, state
persistence, telemetry, marker-replace, TTL staleness,
cleanup actions, gitignore management, tool-return telemetry, and the
generic instruction-segment renderer.

PRD-DIST-2400.
"""

from __future__ import annotations

from trw_mcp.channels._distill_telemetry import (
    emit_tool_call,
    resolve_client_profile,
)
from trw_mcp.channels._gitignore import GITIGNORE_BEGIN, GITIGNORE_END, add_gitignore_entry, list_gitignore_entries
from trw_mcp.channels._lock import ChannelLock, ChannelLockSkip
from trw_mcp.channels._manifest_loader import (
    ChannelManifest,
    ManifestMissingError,
    ManifestValidationError,
    auto_recreate_empty,
    load,
    write,
)
from trw_mcp.channels._manifest_models import (
    CLIENT_CORRECTION_FACTORS,
    DEFAULT_CORRELATION_WINDOW_SECONDS,
    JOIN_KEY_FIELDS,
    MARKER_REGISTRY,
    ChannelEntry,
    ChannelStatus,
    ChannelSurface,
    CleanupAction,
    CleanupConfig,
    CleanupTrigger,
    HumanEditDetection,
    MarkersConfig,
    ProvenanceConfig,
    WriteStrategy,
)
from trw_mcp.channels._provenance import now_utc_iso8601
from trw_mcp.channels._state import (
    ChannelState,
    read_state,
    state_path_for,
    write_state,
)
from trw_mcp.channels._telemetry import (
    CHANNEL_EVENT_SCHEMA_VERSION,
    CHANNEL_EVENT_V1_REQUIRED,
    VALID_EVENT_TYPES,
    append_channel_event,
    validate_record_id,
)

__all__ = [
    "CHANNEL_EVENT_SCHEMA_VERSION",
    "CHANNEL_EVENT_V1_REQUIRED",
    "CLIENT_CORRECTION_FACTORS",
    "DEFAULT_CORRELATION_WINDOW_SECONDS",
    "GITIGNORE_BEGIN",
    "GITIGNORE_END",
    "JOIN_KEY_FIELDS",
    "MARKER_REGISTRY",
    "VALID_EVENT_TYPES",
    "ChannelEntry",
    "ChannelLock",
    "ChannelLockSkip",
    "ChannelManifest",
    "ChannelState",
    "ChannelStatus",
    "ChannelSurface",
    "CleanupAction",
    "CleanupConfig",
    "CleanupTrigger",
    "HumanEditDetection",
    "ManifestMissingError",
    "ManifestValidationError",
    "MarkersConfig",
    "ProvenanceConfig",
    "WriteStrategy",
    "add_gitignore_entry",
    "append_channel_event",
    "auto_recreate_empty",
    "emit_tool_call",
    "list_gitignore_entries",
    "load",
    "now_utc_iso8601",
    "read_state",
    "resolve_client_profile",
    "state_path_for",
    "validate_record_id",
    "write",
    "write_state",
]
