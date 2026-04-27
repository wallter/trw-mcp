"""Operator-facing MCP security status tool.

Reads the authoritative unified ``events-YYYY-MM-DD.jsonl`` stream and, when
present, the legacy ``tool_call_events.jsonl`` projection for back-compat.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field


class MCPSecurityStatus(BaseModel):
    """PRD shape for `trw_mcp_security_status()`."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    registered_servers: list[str] = Field(default_factory=list)
    allowlist_hash: str = ""
    recent_anomalies: list[dict[str, Any]] = Field(default_factory=list)
    quarantined_servers: list[str] = Field(default_factory=list)


def _iter_event_rows(events_dir: Path) -> list[dict[str, Any]]:
    if not events_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()
    candidates = sorted(events_dir.glob("events-*.jsonl"))
    legacy_projection = events_dir / "tool_call_events.jsonl"
    if legacy_projection.exists():
        candidates.append(legacy_projection)
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
    rows = _iter_event_rows(events_dir)
    return MCPSecurityStatus(
        registered_servers=list(registered_servers or []),
        allowlist_hash=allowlist_hash,
        recent_anomalies=_recent_anomalies(rows, now=now),
        quarantined_servers=list(quarantined_servers or []),
    )


def register_mcp_security_status(server: FastMCP) -> None:
    @server.tool()
    def trw_mcp_security_status() -> dict[str, Any]:
        from trw_mcp.server import _app as app_module
        from trw_mcp.state._paths import resolve_trw_dir

        middleware = getattr(app_module, "_mcp_security", None)
        if middleware is not None and hasattr(middleware, "status_snapshot"):
            snapshot: dict[str, Any] = middleware.status_snapshot().model_dump()
            return snapshot
        trw_dir = resolve_trw_dir()
        return compute_security_status(events_dir=trw_dir / "context").model_dump()


__all__ = [
    "MCPSecurityStatus",
    "compute_security_status",
    "register_mcp_security_status",
]
