"""Channel manifest YAML loader, writer, and validator.

Implements load/validate/write for .trw/channels/manifest.yaml.
Performs alias normalization at load time (FR03) and manifest
auto-recovery (FR15 partial — auto_recreate_empty helper).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, ValidationError
from ruamel.yaml import YAML

from trw_mcp.channels._manifest_models import (
    MARKER_REGISTRY,
    ChannelEntry,
)

log = structlog.get_logger(__name__)

MANIFEST_FORMAT_VERSION = "manifest/v1"

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ManifestValidationError(ValueError):
    """Raised when the manifest YAML fails schema validation."""


class ManifestMissingError(FileNotFoundError):
    """Raised when manifest.yaml does not exist at the given path."""


class MarkerCollisionError(ValueError):
    """Raised when a new channel entry's markers collide with existing ones."""


# ---------------------------------------------------------------------------
# Pydantic top-level model
# ---------------------------------------------------------------------------


class ChannelManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    format_version: str
    generated_by: str = "trw-mcp"
    generated_at: str = ""
    channels: list[ChannelEntry] = []


# ---------------------------------------------------------------------------
# Alias normalization (FR03)
# ---------------------------------------------------------------------------


def _normalize_aliases(entry_dict: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy field aliases to canonical names in-place.

    Returns a new dict with canonical keys only.
    """
    d = dict(entry_dict)

    # markers.start: marker_begin | start_marker | marker_start
    for alias in ("marker_begin", "start_marker", "marker_start"):
        if alias in d:
            markers = d.setdefault("markers", {})
            if isinstance(markers, dict) and "start" not in markers:
                markers["start"] = d.pop(alias)
            else:
                d.pop(alias)

    # markers.end: marker_end | end_marker
    for alias in ("marker_end", "end_marker"):
        if alias in d:
            markers = d.setdefault("markers", {})
            if isinstance(markers, dict) and "end" not in markers:
                markers["end"] = d.pop(alias)
            else:
                d.pop(alias)

    # lock_file: lock_path | lock
    for alias in ("lock_path", "lock"):
        if alias in d and "lock_file" not in d:
            d["lock_file"] = d.pop(alias)
        elif alias in d:
            d.pop(alias)

    # tier_default: default_tier
    if "default_tier" in d and "tier_default" not in d:
        d["tier_default"] = d.pop("default_tier")
    elif "default_tier" in d:
        d.pop("default_tier")

    # file: path | target_path  (when string, not to be confused with surface enum)
    for alias in ("path", "target_path"):
        if alias in d and "file" not in d:
            val = d.pop(alias)
            # Only use as file if it looks like a path (contains / or .)
            d["file"] = val
        elif alias in d:
            d.pop(alias)

    # distill_record_types: content_types | record_types
    for alias in ("content_types", "record_types"):
        if alias in d and "distill_record_types" not in d:
            d["distill_record_types"] = d.pop(alias)
        elif alias in d:
            d.pop(alias)

    # cleanup: stale_action + cleanup_trigger → cleanup dict
    if ("stale_action" in d or "cleanup_trigger" in d) and "cleanup" not in d:
        cleanup: dict[str, Any] = {}
        if "stale_action" in d:
            cleanup["action"] = d.pop("stale_action")
        if "cleanup_trigger" in d:
            cleanup["trigger"] = d.pop("cleanup_trigger")
        d["cleanup"] = cleanup
    else:
        d.pop("stale_action", None)
        d.pop("cleanup_trigger", None)

    # operator_tier_override_key: tier_override_key
    if "tier_override_key" in d and "operator_tier_override_key" not in d:
        d["operator_tier_override_key"] = d.pop("tier_override_key")
    elif "tier_override_key" in d:
        d.pop("tier_override_key")

    return d


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load(path: Path) -> ChannelManifest:
    """Load and validate a channel manifest from *path*.

    Raises:
        ManifestMissingError: if the file does not exist.
        ManifestValidationError: if format_version is absent or wrong, or
            if any channel entry fails Pydantic validation.
    """
    if not path.exists():
        raise ManifestMissingError(f"Manifest not found: {path}")

    yaml = YAML(typ="safe")
    with path.open("r", encoding="utf-8") as fh:
        raw: Any = yaml.load(fh)

    if not isinstance(raw, dict):
        raise ManifestValidationError("Manifest must be a YAML mapping")

    fv = raw.get("format_version")
    if not fv:
        raise ManifestValidationError("format_version is required")
    if fv != MANIFEST_FORMAT_VERSION:
        raise ManifestValidationError(f"format_version must be {MANIFEST_FORMAT_VERSION!r}, got {fv!r}")

    raw_channels = raw.get("channels", [])
    if not isinstance(raw_channels, list):
        raise ManifestValidationError("channels must be a list")

    normalized: list[dict[str, Any]] = []
    for i, ch in enumerate(raw_channels):
        if not isinstance(ch, dict):
            raise ManifestValidationError(f"channels[{i}] is not a mapping")
        normalized.append(_normalize_aliases(ch))

    raw["channels"] = normalized

    try:
        manifest = ChannelManifest.model_validate(raw)
    except ValidationError as exc:
        raise ManifestValidationError(str(exc)) from exc

    log.debug("manifest_loaded", path=str(path), channel_count=len(manifest.channels))
    return manifest


def write(manifest: ChannelManifest, path: Path) -> None:
    """Write *manifest* to *path* in round-trip-safe YAML.

    Creates parent directories if needed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    data = manifest.model_dump(mode="json")
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(data, fh)
    log.debug("manifest_written", path=str(path))


def auto_recreate_empty(path: Path) -> None:
    """Write a minimal valid manifest to *path*.

    Used for manifest auto-recovery (FR15 / SYS-04 fix).
    Creates parent directories if needed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    data: dict[str, Any] = {
        "format_version": MANIFEST_FORMAT_VERSION,
        "generated_by": "trw-mcp",
        "generated_at": "",
        "channels": [],
    }
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(data, fh)
    log.warning("manifest_auto_recreated", path=str(path))


def check_marker_collisions(target_file: Path, entry: ChannelEntry) -> None:
    """Scan *target_file* for TRW marker strings that would collide with *entry*.

    Skips aspirational channels (FR16).

    Raises:
        MarkerCollisionError: listing each colliding string found.
    """
    # Use the string value in case use_enum_values serialized it
    status_val = entry.status
    if status_val == "aspirational":
        return

    if not target_file.exists():
        return

    content = target_file.read_text(encoding="utf-8")

    # Collect markers from the entry itself
    entry_markers: list[str] = []
    if isinstance(entry.markers, dict):
        start = entry.markers.get("start", "")
        end = entry.markers.get("end", "")
    else:
        start = entry.markers.start
        end = entry.markers.end
    if start:
        entry_markers.append(start)
    if end:
        entry_markers.append(end)

    # Also check against the canonical registry
    all_markers_to_check = list(MARKER_REGISTRY.values()) + entry_markers

    collisions: list[str] = []
    for marker in all_markers_to_check:
        if marker and marker in content and marker not in collisions:
            collisions.append(marker)

    if collisions:
        raise MarkerCollisionError(f"Marker collision in {target_file}: {collisions!r}")
