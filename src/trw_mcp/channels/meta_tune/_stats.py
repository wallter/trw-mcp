"""Meta-tune stats reporting — ChannelStatsReport + human table.

Computes per-channel correlation stats for reporting via CLI
(channel-doctor stats) and MCP tool (trw_channel_stats).

The tier-current column and throttle_status field were removed 2026-09-22
(RC-014) with the throttle engine (``meta_tune/_throttle.py``) that produced
them — ``tier_default`` was never read to change behavior, so a display
column fed by it carried no signal either.

PRD-DIST-2400 §meta-tune.
"""

from __future__ import annotations

from pathlib import Path

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
    raw_rate: float | None
    adjusted_rate: float | None
    n_events: int
    #: True when the log contains no OUTCOME_EVENT_TYPES event at all, so no
    #: rate was measurable. Distinct from a measured 0.0 — see
    #: ``_correlator.CorrelationResult`` for why nothing has ever produced one.
    outcome_unmeasured: bool = False


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

    entries: list[ChannelStatEntry] = [
        ChannelStatEntry(
            channel_id=r.channel_id,
            client=r.client,
            total_pushes=r.total_pushes,
            correlated=r.correlated,
            raw_rate=r.raw_rate,
            adjusted_rate=r.adj_rate,
            n_events=r.total_pushes,
            outcome_unmeasured=r.outcome_unmeasured,
        )
        for r in results
    ]

    # Sort for deterministic output
    entries.sort(key=lambda e: (e.client, e.channel_id))

    return ChannelStatsReport(
        channels=entries,
        total_events=total_events,
        window_seconds=window_seconds,
        log_path=str(log_path),
    )


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
        f"{'Channel':<28} {'Client':<18} {'Pushes':>7} {'Corr':>6} {'Raw%':>7} {'Adj%':>7}",
        "-" * 90,
    ]

    def _pct(value: float | None) -> str:
        # "n/a" rather than "0.0%": the operator reading this table must be able
        # to tell "we measured none" from "we cannot measure at all".
        return "    n/a" if value is None else f"{value * 100:>6.1f}%"

    lines.extend(
        f"{e.channel_id:<28} {e.client:<18} {e.total_pushes:>7} "
        f"{e.correlated:>6} {_pct(e.raw_rate)} {_pct(e.adjusted_rate)}"
        for e in report.channels
    )
    if any(e.outcome_unmeasured for e in report.channels):
        lines.append("")
        lines.append(
            "n/a: no outcome events in the log, so no correlation rate is "
            "measurable. Nothing in the codebase currently emits one."
        )
    return "\n".join(lines)
