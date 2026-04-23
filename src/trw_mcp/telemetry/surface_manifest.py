"""Surface Manifest — YAML serializer for SurfaceSnapshot (PRD-HPO-MEAS-001 S2).

Stamps the resolved :class:`SurfaceSnapshot` to ``<run>/surface_manifest.yaml``
once per ``trw_session_start``. Round-trips stable YAML so a later
``trw_surface_diff`` tool (Wave 3) can compare two snapshots without
re-walking the filesystem.

Design invariants:

1. **Safe loader only.** Reads use ``yaml.safe_load`` per trw-memory + trw-mcp
   security conventions. Writes use the compact non-flow style.
2. **Atomic write.** Writes go to a ``.tmp`` sibling then ``os.replace`` so a
   crash during write never leaves a half-serialized manifest behind.
3. **Schema-pinned.** :func:`load_manifest` validates through the
   :class:`SurfaceSnapshot` Pydantic model so drift in the on-disk shape
   fails loudly at read time.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
import yaml

from trw_mcp.telemetry.artifact_registry import (
    ComponentFingerprint,
    SurfaceSnapshot,
    resolve_surface_snapshot,
)

logger = structlog.get_logger(__name__)

MANIFEST_FILENAME = "surface_manifest.yaml"


def _component_to_dict(comp: ComponentFingerprint) -> dict[str, Any]:
    return {
        "digest": comp.digest,
        "file_count": comp.file_count,
        "total_bytes": comp.total_bytes,
    }


def _component_from_dict(payload: dict[str, Any]) -> ComponentFingerprint:
    return ComponentFingerprint(
        digest=str(payload.get("digest", "")),
        file_count=int(payload.get("file_count", 0)),
        total_bytes=int(payload.get("total_bytes", 0)),
    )


def snapshot_to_yaml(snapshot: SurfaceSnapshot) -> str:
    """Serialize a :class:`SurfaceSnapshot` to a stable YAML string.

    Keys are sorted alphabetically for diff-stability across runs.
    """
    payload: dict[str, Any] = {
        "snapshot_id": snapshot.snapshot_id,
        "trw_mcp_version": snapshot.trw_mcp_version,
        "framework_version": snapshot.framework_version,
        "generated_at": snapshot.generated_at.isoformat(),
        "components": {key: _component_to_dict(snapshot.components[key]) for key in sorted(snapshot.components)},
    }
    return yaml.safe_dump(payload, sort_keys=True, default_flow_style=False)


def yaml_to_snapshot(raw: str) -> SurfaceSnapshot:
    """Parse a YAML string produced by :func:`snapshot_to_yaml` back into a snapshot.

    Raises:
        ValueError: If the payload is missing required keys or contains a
            non-mapping root.
    """
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        msg = f"surface_manifest payload must be a mapping, got {type(data).__name__}"
        raise ValueError(msg)

    components_raw = data.get("components", {})
    if not isinstance(components_raw, dict):
        msg = "surface_manifest.components must be a mapping"
        raise ValueError(msg)

    components: dict[str, ComponentFingerprint] = {}
    for key, value in components_raw.items():
        if not isinstance(value, dict):
            msg = f"surface_manifest.components[{key!r}] must be a mapping"
            raise ValueError(msg)
        components[str(key)] = _component_from_dict(value)

    # PyYAML returns timezone-aware offsets as strings; SurfaceSnapshot
    # is strict-typed, so parse explicitly.
    generated_raw = data.get("generated_at")
    if isinstance(generated_raw, datetime):
        generated_at = generated_raw
    elif isinstance(generated_raw, str):
        generated_at = datetime.fromisoformat(generated_raw)
    else:
        msg = f"surface_manifest.generated_at must be a datetime or ISO string, got {type(generated_raw).__name__}"
        raise ValueError(msg)

    return SurfaceSnapshot(
        snapshot_id=str(data["snapshot_id"]),
        trw_mcp_version=str(data["trw_mcp_version"]),
        framework_version=str(data["framework_version"]),
        generated_at=generated_at,
        components=components,
    )


def write_manifest(snapshot: SurfaceSnapshot, run_dir: Path) -> Path:
    """Write a :class:`SurfaceSnapshot` to ``<run_dir>/surface_manifest.yaml``.

    The write is atomic: content goes to a temp file in the same directory
    then ``os.replace`` moves it into place. A pre-existing manifest is
    overwritten — callers resolve idempotency at a higher layer (Wave 2
    ``trw_session_start`` check-before-write).
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    target = run_dir / MANIFEST_FILENAME
    data = snapshot_to_yaml(snapshot)

    # Use a temp file in the same directory so os.replace stays on one filesystem.
    fd, tmp_name = tempfile.mkstemp(
        prefix=".surface_manifest.",
        suffix=".yaml.tmp",
        dir=str(run_dir),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
        os.replace(tmp_path, target)
    except OSError:  # justified: cleanup, tmpfile may dangle on rare fs errors
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:  # justified: cleanup, best-effort unlink
                pass
        raise

    logger.info(
        "surface_manifest_written",
        run_dir=str(run_dir),
        snapshot_id=snapshot.snapshot_id,
    )
    return target


def load_manifest(run_dir: Path) -> SurfaceSnapshot | None:
    """Load a manifest from ``<run_dir>/surface_manifest.yaml``.

    Returns ``None`` when the manifest is missing. Raises
    :class:`ValueError` on malformed content — the caller decides whether
    to rewrite or fail.
    """
    path = run_dir / MANIFEST_FILENAME
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8")
    return yaml_to_snapshot(raw)


def stamp_session(run_dir: Path, *, refresh: bool = False) -> SurfaceSnapshot:
    """Resolve + write the surface manifest for a session.

    This is the canonical call site for ``trw_session_start`` (Wave 2
    integration). It reads a resolved :class:`SurfaceSnapshot` via
    :func:`resolve_surface_snapshot`, writes it to the run directory,
    and returns the snapshot so the caller can stamp
    ``surface_snapshot_id`` on session events.

    Args:
        run_dir: Run directory (e.g.
            ``.trw/runs/<task>/<run_id>/``). Will be created if missing.
        refresh: Force a fresh fingerprint computation — defaults to False
            so repeated ``trw_session_start`` calls in the same process
            share the cache.
    """
    snapshot = resolve_surface_snapshot(refresh=refresh)
    write_manifest(snapshot, run_dir)
    return snapshot


__all__ = [
    "MANIFEST_FILENAME",
    "snapshot_to_yaml",
    "yaml_to_snapshot",
    "write_manifest",
    "load_manifest",
    "stamp_session",
]
