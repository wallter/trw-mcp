"""Meta-tune consumer subpackage for the channel telemetry system.

Closes the functional gap: channel-events.jsonl was WRITTEN but never
CONSUMED.  This subpackage provides the full cross-client meta-tune
contract:

- _correlator.py  — join push→outcome by (session_id, file_path) within
                    a rolling time window; raw + adjusted correlation rates
- _stats.py       — ChannelStatsReport + human table for CLI/MCP
- _throttle.py    — auto tier-down when adjusted_rate < per-client threshold;
                    tier-up on recovery (min-N gated)

PRD-DIST-2400 §meta-tune.
"""

from __future__ import annotations

from trw_mcp.channels.meta_tune._correlator import (
    CorrelationEvent,
    CorrelationResult,
    adjusted_rate,
    correlate,
    load_events,
)
from trw_mcp.channels.meta_tune._stats import (
    ChannelStatEntry,
    ChannelStatsReport,
    compute_channel_stats,
    format_stats_table,
)
from trw_mcp.channels.meta_tune._throttle import (
    ThrottleDecision,
    ThrottleVerdict,
    apply_throttle,
    evaluate_throttle,
)

__all__ = [
    "ChannelStatEntry",
    "ChannelStatsReport",
    "CorrelationEvent",
    "CorrelationResult",
    "ThrottleDecision",
    "ThrottleVerdict",
    "adjusted_rate",
    "apply_throttle",
    "compute_channel_stats",
    "correlate",
    "evaluate_throttle",
    "format_stats_table",
    "load_events",
]
