"""Channel manifest YAML loader, writer, and validator.

Implements load/validate/write for .trw/channels/manifest.yaml.
Performs alias normalization at load time (FR03) and manifest
auto-recovery (FR15 — auto_recreate_empty helper + manifest_recovered telemetry).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, ValidationError
from ruamel.yaml import YAML

from trw_mcp.channels._manifest_models import (
    DISTILL_MARKER_KEYS,
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


def _atomic_dump_yaml(data: dict[str, Any], path: Path) -> None:
    """Dump *data* to *path* as round-trip YAML via a temp file + os.replace.

    The manifest is the registry of every channel, so a half-written file is
    catastrophic — load() would raise and all channel operations break. A direct
    ``open("w")`` truncates in place, so a crash (MCP server restart) or a second
    concurrent writer mid-dump can leave an unparseable manifest. Dumping to a
    sibling temp file and ``os.replace``-ing it into position is atomic on POSIX:
    a reader sees either the old or the new manifest, never a partial one. (This
    makes each write crash-safe; it does not serialize concurrent writers, so a
    lost update under true concurrency remains possible — callers that
    read-modify-write should still coordinate.)
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    fd, tmp_str = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.tmp.")
    tmp_path = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.dump(data, fh)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def write(manifest: ChannelManifest, path: Path) -> None:
    """Write *manifest* to *path* in round-trip-safe YAML.

    Atomic (temp file + os.replace) so a crash or concurrent writer can never
    leave a half-written, unparseable manifest. Creates parent dirs if needed.
    """
    _atomic_dump_yaml(manifest.model_dump(mode="json"), path)
    log.debug("manifest_written", path=str(path))


def auto_recreate_empty(path: Path, *, log_path: Path | None = None) -> None:
    """Write a minimal valid manifest to *path*.

    Used for manifest auto-recovery (FR15 / SYS-04 fix).
    Creates parent directories if needed.
    Emits a ``manifest_recovered`` telemetry event (FR15-AC4).

    Args:
        path: Destination path for the recovered manifest.
        log_path: Override for the telemetry log path.  Defaults to the
            standard ``append_channel_event`` resolution (TRW_REPO_ROOT or
            ``.trw/telemetry/channel-events.jsonl``).
    """
    data: dict[str, Any] = {
        "format_version": MANIFEST_FORMAT_VERSION,
        "generated_by": "trw-mcp",
        "generated_at": "",
        "channels": [],
    }
    _atomic_dump_yaml(data, path)
    log.warning("manifest_auto_recreated", path=str(path))

    # FR15-AC4: emit manifest_recovered telemetry event on auto-recovery.
    # Deferred import to avoid circular dependency (_telemetry → (nothing in loader)).
    from trw_mcp.channels._telemetry import append_channel_event

    append_channel_event(
        channel_id="__system__",
        client="__system__",
        event_type="manifest_recovered",
        log_path=log_path,
        outcome="auto_recreated_empty",
        manifest_path=str(path),
    )


def check_marker_collisions(target_file: Path, entry: ChannelEntry) -> None:
    """Scan *target_file* for TRW marker strings that would collide with *entry*.

    Skips aspirational channels (FR16).

    A collision occurs when a distill-channel marker string belonging to a
    DIFFERENT channel is already present in *target_file*.  The entry's own
    configured markers (``entry.markers.start`` and ``entry.markers.end``) are
    excluded from the check — finding them in the file is expected on re-install,
    not a conflict.

    MED-7 fix: generic ceremony markers (``<!-- trw:start -->`` /
    ``<!-- trw:end -->``) are intentionally excluded from the collision scope.
    CLAUDE.md and AGENTS.md files always contain these markers as part of the
    standard TRW bootstrap; treating them as collisions would produce
    false-positives on every standard deployment.  Only distill-channel–specific
    markers (DISTILL_MARKER_KEYS) are checked.

    Raises:
        MarkerCollisionError: listing each colliding foreign marker string found.
    """
    # Use the string value in case use_enum_values serialized it
    status_val = entry.status
    if status_val == "aspirational":
        return

    if not target_file.exists():
        return

    content = target_file.read_text(encoding="utf-8")

    # Collect this entry's own markers so we can exclude them from the check.
    if isinstance(entry.markers, dict):
        own_start = entry.markers.get("start", "")
        own_end = entry.markers.get("end", "")
    else:
        own_start = entry.markers.start
        own_end = entry.markers.end

    own_markers: frozenset[str] = frozenset(m for m in (own_start, own_end) if m)

    # Check only distill-channel marker strings (ceremony markers excluded —
    # see MED-7 rationale in the docstring).  Exclude the entry's own markers.
    foreign_markers = [
        MARKER_REGISTRY[k] for k in DISTILL_MARKER_KEYS if MARKER_REGISTRY[k] and MARKER_REGISTRY[k] not in own_markers
    ]

    collisions: list[str] = []
    for marker in foreign_markers:
        if marker in content and marker not in collisions:
            collisions.append(marker)

    if collisions:
        raise MarkerCollisionError(f"Marker collision in {target_file}: {collisions!r}")
