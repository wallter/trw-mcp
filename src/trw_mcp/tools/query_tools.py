"""MCP query tools — PRD-HPO-MEAS-001 FR-7 + FR-8.

Registers:
- ``trw_query_events(session_id, filters=None)`` — cross-emitter merged
  view of every :class:`HPOTelemetryEvent` written to the unified
  ``events-YYYY-MM-DD.jsonl`` files under a run's ``meta/`` directory.
- ``trw_surface_diff(snapshot_id_a, snapshot_id_b)`` — structured diff
  between two run snapshots: ``{added, removed, changed}`` artifact
  records keyed by ``surface_id``.

Both tools are read-only queries over already-persisted state — no
writes, no network. Fail-open on malformed rows per NFR-8.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
import yaml

from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.telemetry.surface_manifest import MANIFEST_FILENAME

logger = structlog.get_logger(__name__)


def _iter_events_files(run_root: Path) -> list[Path]:
    """Yield every ``events-*.jsonl`` file under ``run_root`` in sort order."""
    if not run_root.exists():
        return []
    return sorted(run_root.glob("**/meta/events-*.jsonl"), key=lambda p: p.as_posix())


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a jsonl file, skipping malformed lines with a WARN log."""
    out: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(
                        "unified_event_jsonl_malformed_line",
                        path=str(path),
                        line_number=i + 1,
                    )
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError:
        logger.warning("unified_event_jsonl_read_failed", path=str(path), exc_info=True)
    return out


def _apply_event_filters(
    events: list[dict[str, Any]],
    filters: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Filter events by a simple equality matcher.

    Supported keys: ``session_id``, ``run_id``, ``event_type``, ``emitter``.
    Unknown keys are silently ignored (forward-compat).
    """
    if not filters:
        return events

    session_id = filters.get("session_id")
    run_id = filters.get("run_id")
    event_type = filters.get("event_type")
    emitter = filters.get("emitter")

    out: list[dict[str, Any]] = []
    for rec in events:
        if session_id is not None and rec.get("session_id") != session_id:
            continue
        if run_id is not None and rec.get("run_id") != run_id:
            continue
        if event_type is not None and rec.get("event_type") != event_type:
            continue
        if emitter is not None and rec.get("emitter") != emitter:
            continue
        out.append(rec)
    return out


def query_events(
    *,
    session_id: str | None = None,
    filters: dict[str, Any] | None = None,
    trw_dir: Path | None = None,
) -> dict[str, Any]:
    """FR-7: return a cross-emitter merged event view for a session.

    Args:
        session_id: Primary filter. When provided, only events with
            matching ``session_id`` are returned. Pass ``None`` to read
            every session (useful for cross-session trend queries).
        filters: Optional extra equality filters (``run_id``,
            ``event_type``, ``emitter``).
        trw_dir: Override the ``.trw`` root path. When None, resolved
            from the current project config.
    """
    resolved = trw_dir if trw_dir is not None else resolve_trw_dir()
    runs_root = resolved / "runs"
    files = _iter_events_files(runs_root)

    merged: list[dict[str, Any]] = []
    for f in files:
        merged.extend(_load_jsonl(f))

    full_filters = dict(filters or {})
    if session_id is not None:
        full_filters["session_id"] = session_id
    filtered = _apply_event_filters(merged, full_filters)

    # Sort chronologically (stable on ts string which is ISO 8601).
    filtered.sort(key=lambda r: str(r.get("ts", "")))
    return {
        "events": filtered,
        "count": len(filtered),
        "source_files": [str(p) for p in files],
    }


def _load_snapshot(run_dir: Path) -> dict[str, Any] | None:
    """Load ``run_surface_snapshot.yaml`` for a run, or None if missing."""
    path = run_dir / "meta" / MANIFEST_FILENAME
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if isinstance(data, dict):
            return data
    except (OSError, yaml.YAMLError):
        logger.warning("surface_snapshot_load_failed", path=str(path), exc_info=True)
    return None


def _find_snapshot_by_id(runs_root: Path, snapshot_id: str) -> dict[str, Any] | None:
    """Locate a snapshot by id across all runs under ``runs_root``."""
    if not runs_root.exists():
        return None
    for manifest in runs_root.glob("**/meta/" + MANIFEST_FILENAME):
        snap = _load_snapshot(manifest.parent.parent)
        if snap is not None and str(snap.get("snapshot_id")) == snapshot_id:
            return snap
    return None


def surface_diff(
    *,
    snapshot_id_a: str,
    snapshot_id_b: str,
    trw_dir: Path | None = None,
) -> dict[str, Any]:
    """FR-8: structured diff between two surface snapshots.

    Returns ``{added, removed, changed}`` — each a list of surface_id
    strings. ``changed`` entries are artifacts present in both snapshots
    with different ``content_hash``.
    """
    resolved = trw_dir if trw_dir is not None else resolve_trw_dir()
    runs_root = resolved / "runs"

    snap_a = _find_snapshot_by_id(runs_root, snapshot_id_a)
    snap_b = _find_snapshot_by_id(runs_root, snapshot_id_b)

    if snap_a is None or snap_b is None:
        return {
            "added": [],
            "removed": [],
            "changed": [],
            "error": "snapshot_not_found",
            "a_found": snap_a is not None,
            "b_found": snap_b is not None,
        }

    arts_a = {
        str(a.get("surface_id")): str(a.get("content_hash", ""))
        for a in snap_a.get("artifacts", [])
        if isinstance(a, dict)
    }
    arts_b = {
        str(a.get("surface_id")): str(a.get("content_hash", ""))
        for a in snap_b.get("artifacts", [])
        if isinstance(a, dict)
    }

    ids_a = set(arts_a.keys())
    ids_b = set(arts_b.keys())
    added = sorted(ids_b - ids_a)
    removed = sorted(ids_a - ids_b)
    changed = sorted(sid for sid in (ids_a & ids_b) if arts_a[sid] != arts_b[sid])

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "snapshot_a_artifact_count": len(arts_a),
        "snapshot_b_artifact_count": len(arts_b),
    }


__all__ = [
    "query_events",
    "surface_diff",
]
