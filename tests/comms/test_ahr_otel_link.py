"""PRD-CORE-349 FR09: the receiver's AHR spans, the accept link to the sender, and what they never carry."""

from __future__ import annotations

import hashlib

from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests._otel_support import assert_keys_registered, assert_no_canary, assert_no_unkeyed_digest
from tests.comms._ahr_support import handoff_doc, inbox, offer, read_back, readback_doc, write
from tests.comms.test_policy import SendScene, scene  # noqa: F401
from trw_mcp.telemetry.otel_ahr import AHR_KEYS, EVENT, HANDOFF_ID, TIER, project_event

_CANARY = "CANARY-7f3e-do-not-export"


def _ahr_spans(exporter: InMemorySpanExporter) -> list:  # type: ignore[type-arg]
    return [span for span in exporter.get_finished_spans() if span.name.startswith("com.trwframework.ahr ")]


def test_accept_links_to_the_senders_span_with_only_the_three_attributes(
    scene: SendScene, otel_spans: InMemorySpanExporter
) -> None:
    handoff = handoff_doc()
    handoff = handoff_doc(objective={**handoff["objective"], "goal": f"Refuse dirty swaps. {_CANARY}"})
    with trace.get_tracer("t").start_as_current_span("sender") as sender:
        message_id = offer(scene, handoff)["receipt"]["message_id"]
    assert read_back(scene, message_id, readback_doc(handoff))["status"] == "ok"
    assert inbox(scene, "impl-2", "accept", message_id)["status"] == "ok"
    content = b"ok\n"
    result = write(scene.formation.project_root, "result.txt", content)
    bound = f"{result}#sha256:{hashlib.sha256(content).hexdigest()}"
    assert inbox(scene, "impl-2", "report", message_id, next_read=bound, handoff={"outcome": "met"})["status"] == "ok"
    assert inbox(scene, "impl-1", "complete", message_id)["status"] == "ok"
    spans = _ahr_spans(otel_spans)
    assert [span.name.split(" ")[1] for span in spans] == ["read_back", "accepted", "reported", "completed"]
    for span in spans:
        assert span.kind is trace.SpanKind.INTERNAL
        assert dict(span.attributes or {}) == {
            HANDOFF_ID: handoff["handoff_id"],
            TIER: "standard",
            EVENT: span.name.split(" ")[1],
        }
    (accepted,) = [span for span in spans if span.name.endswith(" accepted")]
    (link,) = accepted.links
    ((carrier,),) = scene.rows("SELECT traceparent FROM admissions WHERE message_id=?", (message_id,))
    assert link.context.trace_id == sender.get_span_context().trace_id  # the sender's trace
    assert f"{link.context.span_id:016x}" == carrier.split("-")[2]  # the span current at trw_send
    assert link.context.is_remote and len(link.context.trace_state) == 0
    assert dict(link.attributes or {}) == {HANDOFF_ID: handoff["handoff_id"]}
    assert all(not span.links for span in spans if span is not accepted)
    assert_no_canary(spans, _CANARY)
    assert_no_unkeyed_digest(spans)
    assert_keys_registered(spans, AHR_KEYS)


def test_a_digest_shaped_handoff_id_is_never_exported(otel_spans: InMemorySpanExporter) -> None:
    project_event("accepted", "sha256:" + "a" * 64, "standard", None)
    project_event("read_back", "x" + "b" * 64, "standard", None)
    spans = _ahr_spans(otel_spans)
    assert len(spans) == 2 and all(HANDOFF_ID not in (span.attributes or {}) for span in spans)
    assert_no_unkeyed_digest(spans)


def test_events_without_a_receiver_span_emit_nothing(otel_spans: InMemorySpanExporter) -> None:
    for event in ("offered", "declined", "withdrawn", "superseded", "expired", "answered"):
        project_event(event, "h-1", "standard", None)
    assert _ahr_spans(otel_spans) == []
