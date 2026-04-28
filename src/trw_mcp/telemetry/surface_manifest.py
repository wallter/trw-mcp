"""Run Surface Snapshot — YAML serializer for SurfaceRegistry (PRD-HPO-MEAS-001 FR-2).

Stamps the resolved :class:`SurfaceSnapshot` to
``<run_dir>/run_surface_snapshot.yaml`` once per ``trw_session_start``
(Wave 2 wiring). Round-trips stable YAML so the Wave 3 ``trw_surface_diff``
tool can compare two snapshots without re-walking the filesystem.

Design invariants:

1. **Safe loader only.** Reads use ``yaml.safe_load`` per trw-memory +
   trw-mcp security conventions. Writes use compact non-flow style.
2. **Atomic write.** Writes go to a ``.tmp`` sibling then ``os.replace``
   so a crash during write never leaves a half-serialized snapshot behind.
3. **Schema-pinned.** :func:`load_manifest` validates through the
   :class:`SurfaceSnapshot` Pydantic model so drift in the on-disk shape
   fails loudly at read time.
4. **Filename per PRD-FR-2.** The on-disk artifact is
   ``run_surface_snapshot.yaml`` (not ``surface_manifest.yaml`` — the
   latter is the in-package content-addressed canon per FR-1; the run
   copy is the immutable frozen snapshot per FR-2).
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
    SurfaceArtifact,
    SurfaceSnapshot,
    resolve_surface_registry,
)

logger = structlog.get_logger(__name__)

#: Filename mandated by PRD-HPO-MEAS-001 FR-2 (``glob_exists:
#: .trw/runs/*/meta/run_surface_snapshot.yaml``). Do not rename without
#: a PRD update + migration note.
MANIFEST_FILENAME = "run_surface_snapshot.yaml"


def _artifact_to_dict(a: SurfaceArtifact) -> dict[str, Any]:
    return {
        "surface_id": a.surface_id,
        "content_hash": a.content_hash,
        "version": a.version,
        "discovered_at": a.discovered_at.isoformat(),
        "source_path": a.source_path,
    }


def _artifact_from_dict(payload: dict[str, Any]) -> SurfaceArtifact:
    raw_ts = payload.get("discovered_at")
    if isinstance(raw_ts, datetime):
        discovered_at = raw_ts
    elif isinstance(raw_ts, str):
        discovered_at = datetime.fromisoformat(raw_ts)
    else:
        msg = f"artifact.discovered_at must be datetime or ISO string, got {type(raw_ts).__name__}"
        raise TypeError(msg)
    return SurfaceArtifact(
        surface_id=str(payload["surface_id"]),
        content_hash=str(payload["content_hash"]),
        version=str(payload["version"]),
        discovered_at=discovered_at,
        source_path=str(payload["source_path"]),
    )


def snapshot_to_yaml(snapshot: SurfaceSnapshot) -> str:
    """Serialize a :class:`SurfaceSnapshot` to a stable YAML string.

    Artifacts are sorted by ``(surface_id, source_path)`` for diff-stability.
    """
    sorted_artifacts = sorted(snapshot.artifacts, key=lambda a: (a.surface_id, a.source_path))
    payload: dict[str, Any] = {
        "snapshot_id": snapshot.snapshot_id,
        "trw_mcp_version": snapshot.trw_mcp_version,
        "framework_version": snapshot.framework_version,
        "generated_at": snapshot.generated_at.isoformat(),
        "artifacts": [_artifact_to_dict(a) for a in sorted_artifacts],
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
        msg = f"run_surface_snapshot payload must be a mapping, got {type(data).__name__}"
        raise ValueError(msg)  # noqa: TRY004 - public loader contract raises ValueError for schema errors.

    artifacts_raw = data.get("artifacts", [])
    if not isinstance(artifacts_raw, list):
        msg = "run_surface_snapshot.artifacts must be a list"
        raise ValueError(msg)  # noqa: TRY004 - public loader contract raises ValueError for schema errors.

    artifacts: list[SurfaceArtifact] = []
    for idx, entry in enumerate(artifacts_raw):
        if not isinstance(entry, dict):
            msg = f"run_surface_snapshot.artifacts[{idx}] must be a mapping"
            raise ValueError(msg)  # noqa: TRY004 - public loader contract raises ValueError for schema errors.
        artifacts.append(_artifact_from_dict(entry))

    raw_generated = data.get("generated_at")
    if isinstance(raw_generated, datetime):
        generated_at = raw_generated
    elif isinstance(raw_generated, str):
        generated_at = datetime.fromisoformat(raw_generated)
    else:
        msg = f"run_surface_snapshot.generated_at must be a datetime or ISO string, got {type(raw_generated).__name__}"
        raise ValueError(msg)  # noqa: TRY004 - public loader contract raises ValueError for schema errors.

    return SurfaceSnapshot(
        snapshot_id=str(data["snapshot_id"]),
        trw_mcp_version=str(data["trw_mcp_version"]),
        framework_version=str(data["framework_version"]),
        generated_at=generated_at,
        artifacts=tuple(artifacts),
    )


def write_manifest(snapshot: SurfaceSnapshot, run_dir: Path) -> Path:
    """Write a :class:`SurfaceSnapshot` to ``<run_dir>/run_surface_snapshot.yaml``.

    The write is atomic: content goes to a temp file in the same directory
    then ``os.replace`` moves it into place. A pre-existing snapshot is
    overwritten — callers resolve idempotency at a higher layer (Wave 2
    ``trw_session_start`` check-before-write).
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    target = run_dir / MANIFEST_FILENAME
    data = snapshot_to_yaml(snapshot)

    fd, tmp_name = tempfile.mkstemp(
        prefix=".run_surface_snapshot.",
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
        "run_surface_snapshot_written",
        run_dir=str(run_dir),
        snapshot_id=snapshot.snapshot_id,
        artifact_count=len(snapshot.artifacts),
    )
    return target


def load_manifest(run_dir: Path) -> SurfaceSnapshot | None:
    """Load a snapshot from ``<run_dir>/run_surface_snapshot.yaml``.

    Returns ``None`` when the file is missing. Raises
    :class:`ValueError` on malformed content.
    """
    path = run_dir / MANIFEST_FILENAME
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8")
    return yaml_to_snapshot(raw)


def stamp_session(run_dir: Path, *, refresh: bool = False) -> SurfaceSnapshot:
    """Resolve + write ``run_surface_snapshot.yaml`` for a session.

    Canonical call site for ``trw_session_start`` (Wave 2 integration).
    Reads the resolved :class:`SurfaceRegistry` via
    :func:`resolve_surface_registry`, serializes to a frozen snapshot,
    writes it to the run directory, and returns the snapshot so the caller
    can stamp ``surface_snapshot_id`` on session events.

    Args:
        run_dir: Run directory (``<task>/<run_id>/meta/`` by convention).
            Will be created if missing.
        refresh: Force a fresh fingerprint computation — defaults to False
            so repeated ``trw_session_start`` calls share the cache.
    """
    registry = resolve_surface_registry(refresh=refresh)
    snapshot = registry.to_snapshot()
    write_manifest(snapshot, run_dir)
    return snapshot


__all__ = [
    "MANIFEST_FILENAME",
    "load_manifest",
    "snapshot_to_yaml",
    "stamp_session",
    "write_manifest",
    "yaml_to_snapshot",
]
