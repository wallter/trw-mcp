"""TRW attributes on FastMCP's own ``tools/call {name}`` span (PRD-CORE-344 FR02-FR04, FR06).

TRW creates no tool span. The outermost wrapped call sets three attributes on the current span, which
is FastMCP's server span, and ``trw_learn`` adds its dedup outcome. Both are no-ops unless the span is
recording, and neither can raise. The keys are this package's registry copy (OTEL-CONVENTIONS NS-3).
"""

from __future__ import annotations

import re

import structlog
from opentelemetry import trace

logger = structlog.get_logger(__name__)

OPERATION_NAME = "gen_ai.operation.name"
RUN_ID = "com.trwframework.run.id"
TOOL_CALL_ID = "com.trwframework.tool_call.id"
DEDUP_ACTION = "com.trwframework.learn.dedup.action"
REGISTERED_KEYS = frozenset({OPERATION_NAME, RUN_ID, TOOL_CALL_ID, DEDUP_ACTION})

_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_CALL_ID = re.compile(r"[0-9a-f]{8}")
_DEDUP_ACTIONS = frozenset({"skip", "merge", "store"})


def enrich_tool_span(run_id: str | None, tool_call_id: str | None) -> None:
    """Set the operation name and the run/call join keys on the current span, if it is recording."""
    try:
        span = trace.get_current_span()
        if not span.is_recording():
            return
        span.set_attribute(OPERATION_NAME, "execute_tool")
        if run_id and _ID.fullmatch(run_id):
            span.set_attribute(RUN_ID, run_id)
        if tool_call_id and _CALL_ID.fullmatch(tool_call_id):
            span.set_attribute(TOOL_CALL_ID, tool_call_id)
    except Exception:  # justified: fail-open, span enrichment must never change a tool result
        logger.debug("otel_enrich_failed", exc_info=True)


def set_dedup_action(action: str) -> None:
    """Record trw_learn's dedup outcome (``skip``, ``merge`` or ``store``) on the current span."""
    try:
        span = trace.get_current_span()
        if action in _DEDUP_ACTIONS and span.is_recording():
            span.set_attribute(DEDUP_ACTION, action)
    except Exception:  # justified: fail-open, span enrichment must never change a tool result
        logger.debug("otel_enrich_failed", exc_info=True)
