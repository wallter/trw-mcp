"""MCP security status — PRD-CORE-300 slice S3a moved this to
``trw-mcp telemetry security`` (see ``tools/_telemetry_cli.py``), which reads
:func:`compute_security_status` directly, plus a ``trw-mcp doctor`` row that
reports the same status.

Reads the authoritative unified ``events-YYYY-MM-DD.jsonl`` stream from the
context directory and active run meta directories, plus legacy projections.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MCPSecurityStatus(BaseModel):
    """PRD shape for the ``telemetry security`` status document."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    registered_servers: list[str] = Field(default_factory=list)
    allowlist_hash: str = ""
    recent_anomalies: list[dict[str, Any]] = Field(default_factory=list)
    quarantined_servers: list[str] = Field(default_factory=list)


def _iter_event_rows(events_dir: Path, *, run_files_since: float = 0.0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()
    candidates = sorted(events_dir.glob("events-*.jsonl"))
    legacy_projection = events_dir / "tool_call_events.jsonl"
    if legacy_projection.exists():
        candidates.append(legacy_projection)

    # Tool calls with an active run are written under runs/<task>/<run>/meta,
    # not context. Keep the context path for pinless sessions, but include the
    # authoritative per-run stream so readers do not depend on the retired
    # project-wide tool_call_events.jsonl location.
    runs_root = events_dir.parent / "runs"
    if runs_root.is_dir() and not runs_root.is_symlink():
        try:
            task_dirs = sorted(runs_root.iterdir())
        except OSError:
            task_dirs = []
        for task_dir in task_dirs:
            if task_dir.is_symlink() or not task_dir.is_dir():
                continue
            try:
                run_dirs = sorted(task_dir.iterdir())
            except (
                OSError
            ):  # trw-fail-silent-allow: an unreadable task dir has no events to report; the status stays advisory
                continue
            for run_dir in run_dirs:
                if run_dir.is_symlink() or not run_dir.is_dir():
                    continue
                meta_dir = run_dir / "meta"
                if meta_dir.is_symlink() or not meta_dir.is_dir():
                    continue
                # Only run files written inside the anomaly window: a project keeps
                # every run's history, and reading all of it per status call grows
                # without bound.
                run_files = [*sorted(meta_dir.glob("events-*.jsonl")), meta_dir / "tool_call_events.jsonl"]
                candidates.extend(
                    path
                    for path in run_files
                    if path.is_file() and not path.is_symlink() and path.stat().st_mtime >= run_files_since
                )

    for path in candidates:
        try:
            text = path.read_text()
        except OSError:
            continue
        for line in text.splitlines():
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                event_id = parsed.get("event_id")
                if isinstance(event_id, str) and event_id in seen_event_ids:
                    continue
                if isinstance(event_id, str):
                    seen_event_ids.add(event_id)
                rows.append(parsed)
    return rows


def _recent_anomalies(
    rows: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    horizon_hours: int = 24,
) -> list[dict[str, Any]]:
    resolved_now = now or datetime.now(tz=timezone.utc)
    cutoff = resolved_now - timedelta(hours=horizon_hours)
    recent: list[dict[str, Any]] = []
    for row in rows:
        if row.get("event_type") != "mcp_security":
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("decision") != "shadow_anomaly":
            continue
        ts_raw = str(row.get("ts", ""))
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < cutoff:
            continue
        recent.append(
            {
                "ts": ts.isoformat(),
                "server": payload.get("server", ""),
                "tool": payload.get("tool", ""),
                "type": payload.get("anomaly_type", ""),
            }
        )
    return recent


def compute_security_status(
    *,
    events_dir: Path,
    registered_servers: list[str] | None = None,
    allowlist_hash: str = "",
    quarantined_servers: list[str] | None = None,
    now: datetime | None = None,
) -> MCPSecurityStatus:
    resolved_now = now or datetime.now(tz=timezone.utc)
    rows = _iter_event_rows(events_dir, run_files_since=(resolved_now - timedelta(hours=48)).timestamp())
    return MCPSecurityStatus(
        registered_servers=list(registered_servers or []),
        allowlist_hash=allowlist_hash,
        recent_anomalies=_recent_anomalies(rows, now=resolved_now),
        quarantined_servers=list(quarantined_servers or []),
    )


__all__ = [
    "MCPSecurityStatus",
    "compute_security_status",
]
