"""Meta-tune consumer subpackage for the channel telemetry system.

Closes the functional gap: channel-events.jsonl was WRITTEN but never
CONSUMED.  This subpackage provides the cross-client meta-tune contract:

- _correlator.py  — join push→outcome by (session_id, file_path) within
                    a rolling time window; raw + adjusted correlation rates
- _stats.py       — ChannelStatsReport + human table for CLI/MCP

The auto tier-down throttle engine (``_throttle.py``) was removed 2026-09-22
(RC-014): it rewrote ``ChannelEntry.tier_default``, but nothing read that
field to change behavior after the quota enforcement loop it fed was deleted
2026-07-30 (see ``channels/_quota.py`` history). See trw-mcp CHANGELOG.md.

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

__all__ = [
    "ChannelStatEntry",
    "ChannelStatsReport",
    "CorrelationEvent",
    "CorrelationResult",
    "adjusted_rate",
    "compute_channel_stats",
    "correlate",
    "format_stats_table",
    "load_events",
]
