"""Channel manifest substrate for trw-distill client integration.

Phase A + Phase B exports — ChannelEntry schema, locking, provenance,
manifest loader, conflict detection, state persistence, and telemetry.

PRD-DIST-2400.
"""

from __future__ import annotations

from trw_mcp.channels._conflict import (
    RenderLog,
    RenderLogEntry,
    detect_human_edit,
    reconcile,
    write_atomic,
)
from trw_mcp.channels._lock import ChannelLock, ChannelLockSkip
from trw_mcp.channels._manifest_loader import (
    ChannelManifest,
    ManifestMissingError,
    ManifestValidationError,
    MarkerCollisionError,
    auto_recreate_empty,
    check_marker_collisions,
    load,
    write,
)
from trw_mcp.channels._manifest_models import (
    CLIENT_CORRECTION_FACTORS,
    CLIENT_THROTTLE_THRESHOLDS,
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
from trw_mcp.channels._provenance import (
    now_utc_iso8601,
    parse_provenance_comment,
    render_provenance_comment,
    render_provenance_frontmatter,
)
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
    prune_channel_events,
    validate_record_id,
)

__all__ = [
    "CHANNEL_EVENT_SCHEMA_VERSION",
    "CHANNEL_EVENT_V1_REQUIRED",
    "CLIENT_CORRECTION_FACTORS",
    "CLIENT_THROTTLE_THRESHOLDS",
    "DEFAULT_CORRELATION_WINDOW_SECONDS",
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
    "MarkerCollisionError",
    "MarkersConfig",
    "ProvenanceConfig",
    "RenderLog",
    "RenderLogEntry",
    "WriteStrategy",
    "append_channel_event",
    "auto_recreate_empty",
    "check_marker_collisions",
    "detect_human_edit",
    "load",
    "now_utc_iso8601",
    "parse_provenance_comment",
    "prune_channel_events",
    "read_state",
    "reconcile",
    "render_provenance_comment",
    "render_provenance_frontmatter",
    "state_path_for",
    "validate_record_id",
    "write",
    "write_atomic",
    "write_state",
]
