"""Session event merging — canonical location for _merge_session_events.

PRD-FIX-061-FR02: Moved from tools/_deferred_steps_learning.py to resolve
the tools/ -> state/ layer violation.  The tools/ module re-exports this
function for backward compatibility.

The function merges run-level events (events.jsonl) with session-level
events (session-events.jsonl) that land in the fallback path because
trw_session_start fires before trw_init creates the run directory.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.state._helpers import read_jsonl_resilient

logger = structlog.get_logger(__name__)


def _merge_session_events(
    run_events: list[dict[str, object]],
    trw_dir: Path,
) -> list[dict[str, object]]:
    """Merge run-level and session-level events from fallback path.

    FIX-051-FR01/FR05 & FIX-053-FR02: trw_session_start fires before trw_init
    creates the run directory, so its events land in session-events.jsonl instead
    of events.jsonl. This helper merges both sources for ceremony scoring and
    trust increment checks.

    Args:
        run_events: Events from run-level events.jsonl.
        trw_dir: Path to .trw directory for session-events.jsonl lookup.

    Returns:
        Merged event list (session events prepended).
    """
    all_events = list(run_events)
    session_events_path = trw_dir / "context" / "session-events.jsonl"
    # Advisory append-only log: the resilient reader skips torn/undecodable
    # lines per-row (and returns [] when missing/unreadable), so one bad
    # concurrent append no longer drops the entire session-events merge.
    session_events = read_jsonl_resilient(session_events_path)
    if session_events:
        all_events = list(session_events) + all_events
    return all_events
