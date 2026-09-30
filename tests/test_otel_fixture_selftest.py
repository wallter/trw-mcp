"""The shared OTel fixture and privacy helpers catch planted violations (PRD-CORE-342 FR09)."""

from __future__ import annotations

import json

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Link, Status, StatusCode

from tests._otel_support import assert_keys_registered, assert_no_canary, assert_no_unkeyed_digest

CANARY = "CANARY-9d1c"
DIGEST = "a" * 64


def _span(exporter: InMemorySpanExporter, where: str, value: str) -> ReadableSpan:
    tracer = trace.get_tracer("selftest")
    link_ctx = trace.SpanContext(0x1, 0x2, is_remote=True)
    links = [Link(link_ctx, {"k": value})] if where == "link" else []
    with tracer.start_as_current_span("s", links=links) as span:
        if where == "attr":
            span.set_attribute("k", value)
        elif where == "list":
            span.set_attribute("k", ["ok", value])
        elif where == "event":
            span.add_event("e", {"k": value})
        elif where == "status":
            span.set_status(Status(StatusCode.ERROR, value))
    return exporter.get_finished_spans()[-1]


def test_session_provider_is_an_sdk_provider(otel_spans: InMemorySpanExporter) -> None:
    assert isinstance(trace.get_tracer_provider(), TracerProvider)
    with trace.get_tracer("selftest").start_as_current_span("one"):
        pass
    assert [s.name for s in otel_spans.get_finished_spans()] == ["one"]


@pytest.mark.parametrize("where", ["attr", "list", "link", "event", "status"])
def test_assert_no_canary_catches_every_location(otel_spans: InMemorySpanExporter, where: str) -> None:
    span = _span(otel_spans, where, f"x {CANARY} y")
    with pytest.raises(AssertionError):
        assert_no_canary([span], CANARY)


@pytest.mark.parametrize("where", ["attr", "list", "link", "event"])
@pytest.mark.parametrize("value", [f"sha256:{DIGEST}", f"id={DIGEST}"])
def test_assert_no_unkeyed_digest_catches_attribute_values(
    otel_spans: InMemorySpanExporter, where: str, value: str
) -> None:
    span = _span(otel_spans, where, value)
    with pytest.raises(AssertionError):
        assert_no_unkeyed_digest([span])


def test_clean_span_passes_every_helper(otel_spans: InMemorySpanExporter) -> None:
    span = _span(otel_spans, "attr", "run-20260929")
    assert_no_canary([span], CANARY)
    assert_no_unkeyed_digest([span])  # trace/span ids are hex but never inspected
    assert_keys_registered([span], {"k"})
    with pytest.raises(AssertionError):
        assert_keys_registered([span], {"other"})
    # a 65-hex run is not an exact 64-run digest
    assert_no_unkeyed_digest([_span(otel_spans, "attr", "b" * 65)])


def test_digest_helper_reads_otlp_json_lines() -> None:
    def line(value: str) -> str:
        attr = {"key": "k", "value": {"stringValue": value}}
        span = {"traceId": "1" * 32, "spanId": "2" * 16, "attributes": [attr]}
        return json.dumps({"resourceSpans": [{"scopeSpans": [{"spans": [span]}]}]})

    assert_no_unkeyed_digest([line("clean")])
    with pytest.raises(AssertionError):
        assert_no_unkeyed_digest([line(f"sha256:{DIGEST}")])
