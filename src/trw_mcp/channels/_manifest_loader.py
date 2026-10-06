"""Channel manifest YAML loader, writer, and validator.

Implements load/validate/write for .trw/channels/manifest.yaml.
Performs alias normalization at load time (FR03) and manifest
auto-recovery (FR15 — auto_recreate_empty helper + manifest_recovered telemetry).
"""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from trw_mcp._checkout_write import record_run_write
from trw_mcp.channels._manifest_models import ChannelEntry

log = structlog.get_logger(__name__)

MANIFEST_FORMAT_VERSION = "manifest/v1"

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ManifestValidationError(ValueError):
    """Raised when the manifest YAML fails schema validation."""


class ManifestMissingError(FileNotFoundError):
    """Raised when manifest.yaml does not exist at the given path."""


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

    # tier_default / tier_min / default_tier were removed from ChannelEntry
    # 2026-09-22 (RC-014): nothing read them to change behavior. They are not
    # dropped here, so a read-only load of an old manifest still fails loudly;
    # update-project migrates the file on disk instead
    # (migrate_retired_entry_keys, called by the bootstrap merge).

    # file: path | target_path  (when string, not to be confused with surface enum)
    for alias in ("path", "target_path"):
        if alias in d and "file" not in d:
            val = d.pop(alias)
            # Only use as file if it looks like a path (contains / or .)
            d["file"] = val
        elif alias in d:
            d.pop(alias)

    # distill_record_types and its legacy aliases were removed from ChannelEntry
    # (never read by any consumer). An old manifest that still carries them must
    # keep loading, so they are DROPPED rather than renamed onto a field that no
    # longer exists — renaming would turn a stale key into a ValidationError.
    for alias in ("content_types", "record_types", "distill_record_types"):
        d.pop(alias, None)

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

    # Same for the tier-override key and the four emit_on_* / session_correlation
    # flags: all were authored per-channel with real variation and read by
    # nothing, so they are dropped on load for backward compatibility.
    for removed in (
        "tier_override_key",
        "operator_tier_override_key",
        "client_version_min",
        "sidecar_schema",
        "sidecar_path",
        "emit_on_ttl_skip",
        "emit_on_conflict_skip",
        "emit_on_lock_skip",
        "session_correlation",
    ):
        d.pop(removed, None)

    return d


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


#: Entry keys a pre-6.0.0 manifest carries that ChannelEntry no longer accepts (RC-014).
RETIRED_ENTRY_KEYS: tuple[str, ...] = ("tier_default", "tier_min", "default_tier")


def _summarize_validation_error(exc: ValidationError) -> str:
    """One line for a pydantic error: each distinct problem once, with how many entries share it.

    ``str(ValidationError)`` prints three lines per error, each ending in the same "For further information
    visit ..." URL, so a 13-entry manifest with two stale keys printed 26 copies of that URL.
    """
    counts: dict[tuple[str, str], int] = {}
    for err in exc.errors():
        field = str(err["loc"][-1]) if err["loc"] else "manifest"
        key = (field, str(err["msg"]))
        counts[key] = counts.get(key, 0) + 1
    total = sum(counts.values())
    parts = [f"{field}: {msg}" + (f" (x{n})" if n > 1 else "") for (field, msg), n in counts.items()]
    return f"{total} validation error{'s' if total != 1 else ''}: " + "; ".join(parts)


def _validate_raw(raw: Any) -> ChannelManifest:
    """Validate a parsed manifest mapping (format, channel shape, entry schema) after alias normalization."""
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
        return ChannelManifest.model_validate(raw)
    except ValidationError as exc:
        raise ManifestValidationError(_summarize_validation_error(exc)) from exc


def _dump_rt(data: Any) -> str:
    """Round-trip YAML text for *data*: the one serializer every manifest write uses."""
    yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    buf = io.StringIO()
    yaml.dump(data, buf)
    return buf.getvalue()


def drop_retired_entry_keys(text: str) -> tuple[str, int]:
    """Return *text* with every :data:`RETIRED_ENTRY_KEYS` key removed from each channel entry, and the count.

    Round-trip YAML keeps every other key, value, comment and order. A text with nothing to drop (or that is
    not a manifest-shaped mapping) comes back unchanged with a count of 0. Raises ``ManifestValidationError``
    for unparseable YAML.
    """
    try:
        data: Any = YAML(typ="rt").load(text)
    except YAMLError as exc:
        raise ManifestValidationError(f"Manifest is not valid YAML: {exc}") from exc
    channels = data.get("channels") if isinstance(data, dict) else None
    dropped = 0
    for entry in channels if isinstance(channels, list) else []:
        if isinstance(entry, dict):
            for key in RETIRED_ENTRY_KEYS:
                if key in entry:
                    del entry[key]
                    dropped += 1
    return (_dump_rt(data), dropped) if dropped else (text, 0)


def migrate_retired_entry_keys(path: Path) -> int:
    """Drop the retired tier keys from the manifest at *path* in place; return how many keys were dropped.

    The file is rewritten only when the result then validates, so a manifest with any other problem is left
    byte-identical and that problem raised as ``ManifestValidationError``. Returns 0, writing nothing, when
    there is nothing to drop.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ManifestValidationError(f"Manifest could not be read: {exc}") from exc
    new_text, dropped = drop_retired_entry_keys(text)
    if not dropped:
        return 0
    _validate_raw(YAML(typ="safe").load(new_text))
    _atomic_write_text(new_text, path)
    log.info("manifest_retired_keys_dropped", path=str(path), dropped=dropped)
    return dropped


def load(path: Path) -> ChannelManifest:
    """Load and validate a channel manifest from *path*.

    Raises:
        ManifestMissingError: if the file does not exist.
        ManifestValidationError: if the file is not parseable YAML, if
            format_version is absent or wrong, or if any channel entry fails
            Pydantic validation.
    """
    if not path.exists():
        raise ManifestMissingError(f"Manifest not found: {path}")

    yaml = YAML(typ="safe")
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw: Any = yaml.load(fh)
    except YAMLError as exc:
        # A syntactically malformed manifest is a VALIDATION failure, not an
        # unhandled crash: callers (channel_doctor, the bootstrap merge)
        # document a never-raises contract and only catch the two Manifest*
        # errors, so a raw ruamel YAMLError would escape as a traceback.
        raise ManifestValidationError(f"Manifest is not valid YAML: {exc}") from exc

    manifest = _validate_raw(raw)
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
    _atomic_write_text(_dump_rt(data), path)


def _atomic_write_text(text: str, path: Path) -> None:
    """Publish *text* at *path* via a sibling temp file + os.replace, recording the bytes as this run's write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.tmp.")
    tmp_path = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        published = tmp_path.read_bytes()  # private temp file: exactly the bytes about to be published
        os.replace(tmp_path, path)
        record_run_write(path, published)  # FB-01-KI1-RACE restore proof
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


def auto_recreate_empty(path: Path, *, log_path: Path | None = None, reason: str = "corrupt") -> None:
    """Write a minimal valid manifest to *path*.

    Creates parent directories if needed.

    Args:
        path: Destination path for the recovered manifest.
        log_path: Override for the telemetry log path.  Defaults to the
            standard ``append_channel_event`` resolution (TRW_REPO_ROOT or
            ``.trw/telemetry/channel-events.jsonl``).
        reason: ``"corrupt"`` (default, FR15 / SYS-04 fix) — the manifest
            existed but failed validation, which is a real recovery: logs at
            WARNING and emits a ``manifest_recovered`` telemetry event
            (FR15-AC4). ``"missing"`` — no manifest existed yet, which is the
            normal shape of a first ``init-project``: logs at INFO with no
            recovery event, since nothing was recovered. Conflating the two
            under one bare-except caller used to warn (and page on the
            telemetry stream) every first-time install.
    """
    data: dict[str, Any] = {
        "format_version": MANIFEST_FORMAT_VERSION,
        "generated_by": "trw-mcp",
        "generated_at": "",
        "channels": [],
    }
    _atomic_dump_yaml(data, path)

    if reason == "missing":
        log.info("manifest_created", path=str(path))
        return

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
