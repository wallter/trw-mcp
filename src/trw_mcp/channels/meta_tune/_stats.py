"""Meta-tune stats reporting — ChannelStatsReport + human table.

Computes per-channel correlation + throttle status for reporting
via CLI (channel-doctor stats) and MCP tool (trw_channel_stats).

PRD-DIST-2400 §meta-tune.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict

from trw_mcp.channels._manifest_models import DEFAULT_CORRELATION_WINDOW_SECONDS
from trw_mcp.channels.meta_tune._correlator import CorrelationResult, correlate, load_events

log = structlog.get_logger(__name__)

__all__ = [
    "ChannelStatEntry",
    "ChannelStatsReport",
    "compute_channel_stats",
    "format_stats_table",
]


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ChannelStatEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    channel_id: str
    client: str
    total_pushes: int
    correlated: int
    raw_rate: float
    adjusted_rate: float
    n_events: int
    tier_current: str
    throttle_status: str  # "ok" | "insufficient_data" | "throttle_down" | "throttle_clear"


class ChannelStatsReport(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    channels: list[ChannelStatEntry] = []
    total_events: int = 0
    window_seconds: int = DEFAULT_CORRELATION_WINDOW_SECONDS
    log_path: str = ""


# ---------------------------------------------------------------------------
# Compute
# ---------------------------------------------------------------------------


def compute_channel_stats(
    log_path: Path,
    *,
    window_seconds: int = DEFAULT_CORRELATION_WINDOW_SECONDS,
    manifest_path: Path | None = None,
) -> ChannelStatsReport:
    """Load events from *log_path* and compute per-channel correlation stats.

    Fail-open: returns an empty report if log is missing or unreadable.
    """
    events = load_events(log_path)
    total_events = len(events)

    if not events:
        return ChannelStatsReport(
            channels=[],
            total_events=0,
            window_seconds=window_seconds,
            log_path=str(log_path),
        )

    results: list[CorrelationResult] = correlate(events, window_seconds=window_seconds)

    # Optionally enrich with tier from manifest
    tier_map = _load_tier_map(manifest_path)

    # Build throttle decisions inline (lightweight — no side effects)
    from trw_mcp.channels.meta_tune._throttle import evaluate_throttle

    entries: list[ChannelStatEntry] = []
    for r in results:
        tier_key = f"{r.client}:{r.channel_id}"
        tier_current = tier_map.get(tier_key, "unknown")

        # Build a minimal stat dict for throttle evaluation
        stat_dict: dict[str, Any] = {
            "adjusted_rate": r.adj_rate,
            "total_pushes": r.total_pushes,
        }
        decision = evaluate_throttle(r.channel_id, r.client, stat_dict)
        throttle_status = decision.verdict.value

        entries.append(
            ChannelStatEntry(
                channel_id=r.channel_id,
                client=r.client,
                total_pushes=r.total_pushes,
                correlated=r.correlated,
                raw_rate=r.raw_rate,
                adjusted_rate=r.adj_rate,
                n_events=r.total_pushes,
                tier_current=tier_current,
                throttle_status=throttle_status,
            )
        )

    # Sort for deterministic output
    entries.sort(key=lambda e: (e.client, e.channel_id))

    return ChannelStatsReport(
        channels=entries,
        total_events=total_events,
        window_seconds=window_seconds,
        log_path=str(log_path),
    )


def _load_tier_map(manifest_path: Path | None) -> dict[str, str]:
    """Return {client:channel_id -> tier_default} from manifest. Fail-open."""
    if manifest_path is None or not manifest_path.exists():
        return {}
    try:
        from trw_mcp.channels._manifest_loader import load

        manifest = load(manifest_path)
        return {f"{entry.client}:{entry.id}": entry.tier_default for entry in manifest.channels}
    except Exception as exc:
        log.debug(
            "meta_tune_stats_tier_map_load_failed",
            path=str(manifest_path),
            error=str(exc),
            outcome="tier_map_empty",
        )
        return {}


# ---------------------------------------------------------------------------
# Human table
# ---------------------------------------------------------------------------


def format_stats_table(report: ChannelStatsReport) -> str:
    """Render a human-readable stats table for CLI display."""
    if not report.channels:
        return f"No channel stats (0 events in log: {report.log_path})\nWindow: {report.window_seconds}s"

    lines: list[str] = [
        f"Channel Stats  events={report.total_events}  window={report.window_seconds}s",
        f"Log: {report.log_path}",
        "",
        f"{'Channel':<28} {'Client':<18} {'Pushes':>7} {'Corr':>6} {'Raw%':>7} {'Adj%':>7} {'Tier':<8} {'Status'}",
        "-" * 100,
    ]
    lines.extend(
        f"{e.channel_id:<28} {e.client:<18} {e.total_pushes:>7} "
        f"{e.correlated:>6} {e.raw_rate * 100:>6.1f}% {e.adjusted_rate * 100:>6.1f}% "
        f"{e.tier_current:<8} {e.throttle_status}"
        for e in report.channels
    )
    return "\n".join(lines)
