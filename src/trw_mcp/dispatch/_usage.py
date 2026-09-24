"""A dispatched child's token report and policy, recorded as run events (PRD-CORE-290-FR01/FR03).

Belongs to the ``trw_mcp.dispatch`` package. The figures are what the child's
client reported about itself (e.g. grok's and claude's JSON ``usage`` block), so
the ledger labels them ``self-reported``. A client that reports nothing records
nothing: unobservable usage is absent, never zero. Each child is keyed by
``child_id`` so a result polled twice counts once. The policy event records what
each dispatch requested versus what its child's command line carried.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.dispatch._policy import policy_record
from trw_mcp.dispatch._types import DispatchRequest, DispatchResult

__all__ = ["record_child_usage", "record_dispatch_policy"]

logger = structlog.get_logger(__name__)

#: The child's usage keys TRW records, as the clients spell them.
_TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens")


def _active_run() -> Path | None:
    from trw_mcp.state._paths import get_pinned_run

    return get_pinned_run()


def record_dispatch_policy(req: DispatchRequest, child_id: str) -> dict[str, dict[str, object]]:
    """Append the dispatch's policy record to the active run (when there is one) and return it."""
    record = policy_record(req)
    run = _active_run()
    if run is not None:
        from trw_mcp.telemetry.event_base import DispatchPolicyEvent
        from trw_mcp.telemetry.unified_events import emit

        payload: dict[str, object] = {"child_id": child_id, "client": req.client, **record}
        emit(DispatchPolicyEvent(session_id="", run_id=run.name, payload=payload), run_dir=run, fallback_dir=None)
    return record


def record_child_usage(result: DispatchResult, child_id: str) -> bool:
    """Append the child's token report to the active run; ``False`` when there is none to record."""
    usage = result.structured.get("usage") if isinstance(result.structured, dict) else None
    tokens = {key: usage[key] for key in _TOKEN_KEYS if isinstance(usage, dict) and isinstance(usage.get(key), int)}
    run = _active_run()
    if not tokens or run is None:
        return False
    from trw_mcp.telemetry.event_base import DispatchUsageEvent
    from trw_mcp.telemetry.unified_events import emit

    payload: dict[str, object] = {"child_id": child_id, "client": result.client, **tokens}
    written = emit(DispatchUsageEvent(session_id="", run_id=run.name, payload=payload), run_dir=run, fallback_dir=None)
    logger.info("dispatch_child_usage_recorded", child_id=child_id, client=result.client, written=written)
    return written
