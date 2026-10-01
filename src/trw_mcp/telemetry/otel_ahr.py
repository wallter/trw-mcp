"""AHR receiver spans (PRD-CORE-349 FR09; OTEL-CONVENTIONS §7.4, §9, §10).

One INTERNAL span ``com.trwframework.ahr {event}`` per stored ``read_back``, ``accepted``, ``reported``
and ``completed`` event. ``accepted`` carries a link, supplied at creation, to the sender's span named
by the carrier's stored ``traceparent`` (PRD-CORE-342), with an empty trace state. Attributes are
exactly ``handoff_id`` (only when it is an opaque id), ``tier`` and ``event``: no record content, and
no digest (operator decision 3: no unkeyed content digest in any export). A span points at the event
log; it is never proof, nothing reads it back, and every fault is swallowed here, so telemetry can
never refuse or fail an AHR event. Without a configured tracer provider it is a no-op.
"""

from __future__ import annotations

import re

import structlog
from opentelemetry import trace

from trw_mcp.telemetry.otel_propagation import link_from_traceparent

logger = structlog.get_logger(__name__)

_tracer = trace.get_tracer("trw_mcp.comms.ahr")
_A = "com.trwframework.ahr."
HANDOFF_ID = f"{_A}handoff_id"
TIER = f"{_A}tier"
EVENT = f"{_A}event"
AHR_KEYS = frozenset({HANDOFF_ID, TIER, EVENT})
SPAN_EVENTS = frozenset({"read_back", "accepted", "reported", "completed"})
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_DIGEST_LIKE = re.compile(r"sha256:|[0-9a-fA-F]{64}")


def _join_key(handoff_id: str) -> str | None:
    """The handoff id as the join key, or None when it is not opaque or could carry a digest."""
    if not _ID.fullmatch(handoff_id) or _DIGEST_LIKE.search(handoff_id):
        return None
    return handoff_id


def project_event(event: str, handoff_id: str, tier: str, traceparent: object = None) -> None:
    """Emit the receiver span for one stored AHR event; never raises."""
    if event not in SPAN_EVENTS:
        return
    try:
        key = _join_key(handoff_id)
        attributes: dict[str, str] = {TIER: tier, EVENT: event}
        if key is not None:
            attributes[HANDOFF_ID] = key
        links: list[trace.Link] = []
        if event == "accepted":
            carrier = link_from_traceparent(traceparent)
            if carrier is not None:
                links.append(trace.Link(carrier.context, {HANDOFF_ID: key} if key is not None else None))
        span = _tracer.start_span(
            f"com.trwframework.ahr {event}",
            kind=trace.SpanKind.INTERNAL,
            attributes=attributes,
            links=links,
            record_exception=False,
            set_status_on_exception=False,
        )
        span.end()
    except Exception:  # trw-fail-silent-allow: a span is a pointer, never a gate; the event is already stored
        logger.info("otel_projection_failed", surface="ahr")


__all__ = ["AHR_KEYS", "EVENT", "HANDOFF_ID", "SPAN_EVENTS", "TIER", "project_event"]
