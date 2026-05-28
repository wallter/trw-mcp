"""Channel manifest substrate for trw-distill client integration.

Phase A foundation exports — ChannelEntry schema, locking, provenance,
and manifest loader. Phase B/C/D exports will be added in downstream PRDs.

PRD-DIST-2400.
"""

from __future__ import annotations

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

__all__ = [
    "CLIENT_CORRECTION_FACTORS",
    "CLIENT_THROTTLE_THRESHOLDS",
    "DEFAULT_CORRELATION_WINDOW_SECONDS",
    "JOIN_KEY_FIELDS",
    "MARKER_REGISTRY",
    "ChannelEntry",
    "ChannelLock",
    "ChannelLockSkip",
    "ChannelManifest",
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
    "WriteStrategy",
    "auto_recreate_empty",
    "check_marker_collisions",
    "load",
    "now_utc_iso8601",
    "parse_provenance_comment",
    "render_provenance_comment",
    "render_provenance_frontmatter",
    "write",
]
