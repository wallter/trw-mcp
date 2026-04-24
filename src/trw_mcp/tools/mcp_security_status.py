"""MCP security status operator tool (PRD-INFRA-SEC-001 FR-5 / FR-7).

Registers ``trw_mcp_security_status()`` which reports:

* ``registry_mode`` — ``observe`` (v1 default) or ``enforce``.
* ``shadow_clock_start`` — ISO timestamp when the 3-week shadow window began.
* ``anomaly_count_last_24h`` — count of anomaly events in the last 24h.
* ``capability_scope_denials_last_24h`` — count of ``shadow_deny`` decisions.
* ``enforce_mode`` — boolean; always ``False`` in v1.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import structlog
import yaml
from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)


class MCPSecurityStatus(BaseModel):
    """Operator-facing security status shape (PRD FR-7)."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    registry_mode: str = Field(default="observe")
    shadow_clock_start: str = ""
    anomaly_count_last_24h: int = 0
    capability_scope_denials_last_24h: int = 0
    enforce_mode: bool = False


def _load_shadow_clock(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):  # justified: boundary, tolerate corrupt shadow-clock file
        logger.warning("mcp_status_shadow_clock_unreadable", path=str(path))
        return ""
    if isinstance(raw, dict):
        val = raw.get("started_at")
        return str(val) if val else ""
    return ""


def _iter_event_rows(events_dir: Path) -> list[dict[str, Any]]:
    """Read all ``events-*.jsonl`` rows under ``events_dir``. Fail-open."""
    if not events_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(events_dir.glob("events-*.jsonl")):
        try:
            text = path.read_text()
        except OSError:  # justified: boundary, tolerate missing/unreadable files
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:  # justified: boundary, skip malformed row
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


def _count_recent(
    rows: list[dict[str, Any]],
    *,
    decision_filter: str | None = None,
    anomaly: bool = False,
    horizon_hours: int = 24,
    now: datetime | None = None,
) -> int:
    """Count MCPSecurityEvent rows matching filters within the horizon."""
    now = now or datetime.now(tz=timezone.utc)
    cutoff = now - timedelta(hours=horizon_hours)
    count = 0
    for row in rows:
        if row.get("event_type") != "mcp_security":
            continue
        ts_raw = row.get("ts", "")
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except ValueError:  # justified: boundary, skip unparseable timestamp
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < cutoff:
            continue
        payload = row.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if anomaly and payload.get("decision") == "shadow_anomaly":
            count += 1
        elif decision_filter and payload.get("decision") == decision_filter:
            count += 1
    return count


def compute_security_status(
    *,
    shadow_clock_path: Path,
    events_dir: Path,
    enforce_mode: bool = False,
    now: datetime | None = None,
) -> MCPSecurityStatus:
    """Build an :class:`MCPSecurityStatus` from on-disk state."""
    rows = _iter_event_rows(events_dir)
    return MCPSecurityStatus(
        registry_mode="enforce" if enforce_mode else "observe",
        shadow_clock_start=_load_shadow_clock(shadow_clock_path),
        anomaly_count_last_24h=_count_recent(rows, anomaly=True, now=now),
        capability_scope_denials_last_24h=_count_recent(
            rows, decision_filter="shadow_deny", now=now
        ),
        enforce_mode=enforce_mode,
    )


def register_mcp_security_status(server: FastMCP) -> None:
    """Register ``trw_mcp_security_status`` on the given FastMCP server."""
    # PRD-INFRA-SEC-001 FR-9 per-dispatch consult (sprint-96 carry-forward
    # a): deferred import to avoid circular dep with trw_mcp.server._app.
    from trw_mcp.server._security_hook import consult_mcp_security

    @server.tool()
    def trw_mcp_security_status() -> dict[str, Any]:
        """Return the current MCP security status (PRD-INFRA-SEC-001 FR-7).

        v1 always reports ``registry_mode="observe"`` and
        ``enforce_mode=False``. A Sprint 97+ decision gate flips these once
        the 3-week shadow window closes.
        """
        consult_mcp_security("trw_mcp_security_status", {}, "", None)
        from trw_mcp.state._paths import resolve_trw_dir

        trw_dir = resolve_trw_dir()
        status = compute_security_status(
            shadow_clock_path=trw_dir / "security" / "mcp_shadow_start.yaml",
            events_dir=trw_dir / "context",
            enforce_mode=False,
        )
        return status.model_dump()


__all__ = [
    "MCPSecurityStatus",
    "compute_security_status",
    "register_mcp_security_status",
]
