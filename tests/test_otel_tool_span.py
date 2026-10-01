"""TRW attributes on FastMCP's own tools/call span; one span per call (PRD-CORE-344 FR02-FR04, FR06, NFR02-03)."""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import structlog
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from tests._otel_support import assert_keys_registered, assert_no_canary, assert_no_unkeyed_digest

CANARY = "CANARY-5b0e-tool-text"
# FastMCP's own tools/call keys (upstream allowlist) plus TRW's registry.
_UPSTREAM = {
    "gen_ai.tool.name",
    "rpc.system",
    "rpc.method",
    "rpc.service",
    "mcp.method.name",
    "mcp.session.id",
    "mcp.resource.uri",
    "fastmcp.server.name",
    "fastmcp.component.type",
    "fastmcp.component.key",
    "fastmcp.provider.type",
    "error.type",
    "jsonrpc.request.id",
    "mcp.protocol.version",
    "network.transport",
    "network.protocol.name",
}


@pytest.fixture
def server(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """A real FastMCP server whose tools are wrapped by TRW's production wrapper."""
    from fastmcp import FastMCP

    from trw_mcp.models.config import TRWConfig, reload_config
    from trw_mcp.server import _tools

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_project))
    monkeypatch.chdir(tmp_project)
    reload_config(TRWConfig())
    srv = FastMCP("otel-tool-span")

    @srv.tool()
    def sync_tool(text: str) -> dict[str, object]:
        """Use when testing."""
        return {"echo": text, "call": structlog.contextvars.get_contextvars().get("tool_call_id")}

    @srv.tool()
    async def async_tool(text: str) -> dict[str, object]:
        """Use when testing."""
        await asyncio.sleep(0)
        return {"echo": text, "call": structlog.contextvars.get_contextvars().get("tool_call_id")}

    @srv.tool()
    def raising_tool(text: str) -> dict[str, object]:
        """Use when testing."""
        raise ValueError(f"{CANARY} {text}")

    @srv.tool()
    def outer_tool() -> dict[str, object]:
        """Use when testing."""
        from trw_mcp.telemetry.tool_call_timing import wrap_tool

        inner = wrap_tool(lambda: "inner", tool_name="inner_tool")
        return {"inner": inner(), "call": structlog.contextvars.get_contextvars().get("tool_call_id")}

    _tools._apply_security_consult_wrapping(srv)
    yield srv
    reload_config(None)


def _call(srv: Any, name: str, args: dict[str, object]) -> Any:
    from fastmcp import Client

    async def go() -> Any:
        async with Client(srv) as client:
            return await client.call_tool(name, args, raise_on_error=False)

    return asyncio.run(go())


def _tool_spans(exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    """The server-side tools/call spans (the in-memory Client also emits its own CLIENT span)."""
    return [s for s in exporter.get_finished_spans() if s.name.startswith("tools/call") and s.kind is SpanKind.SERVER]


@pytest.mark.parametrize("tool", ["sync_tool", "async_tool"])
def test_one_enriched_tools_call_span_per_call(server: Any, otel_spans: InMemorySpanExporter, tool: str) -> None:
    from trw_mcp.telemetry._tool_span_attrs import OPERATION_NAME, REGISTERED_KEYS, TOOL_CALL_ID

    result = _call(server, tool, {"text": CANARY})
    assert result.data["echo"] == CANARY
    (span,) = _tool_spans(otel_spans)
    assert span.name == f"tools/call {tool}"
    attrs = dict(span.attributes or {})
    assert attrs[OPERATION_NAME] == "execute_tool"
    assert attrs[TOOL_CALL_ID] == result.data["call"]
    assert re.fullmatch(r"[0-9a-f]{8}", str(attrs[TOOL_CALL_ID]))
    names = [s.name for s in otel_spans.get_finished_spans()]
    assert not any(n == "gen_ai.execute_tool" or n.startswith(("tool.", "execute_tool")) for n in names)
    assert_keys_registered([span], REGISTERED_KEYS | _UPSTREAM)
    spans = list(otel_spans.get_finished_spans())
    assert_no_canary(spans, CANARY)
    assert_no_unkeyed_digest(spans)


def test_run_id_is_set_when_a_run_is_resolved(
    server: Any, otel_spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch, tmp_project: Path
) -> None:
    import trw_mcp.telemetry._tool_call_emit as emit
    from trw_mcp.telemetry._tool_span_attrs import RUN_ID

    run_dir = tmp_project / ".trw" / "runs" / "task" / "20260929T000000Z-abcd1234"
    (run_dir / "meta").mkdir(parents=True)
    monkeypatch.setattr(emit, "_resolve_run_dir", lambda _ctx: run_dir)
    _call(server, "sync_tool", {"text": "x"})
    (span,) = _tool_spans(otel_spans)
    assert span.attributes[RUN_ID] == "20260929T000000Z-abcd1234"


def test_nested_wrapped_call_does_not_overwrite(
    server: Any, otel_spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    import trw_mcp.telemetry._tool_call_emit as emit
    from trw_mcp.telemetry._tool_span_attrs import TOOL_CALL_ID

    enriched: list[tuple[str | None, str | None]] = []
    real = emit.enrich_tool_span
    monkeypatch.setattr(emit, "enrich_tool_span", lambda r, c: (enriched.append((r, c)), real(r, c)))
    result = _call(server, "outer_tool", {})
    (span,) = _tool_spans(otel_spans)
    assert span.attributes[TOOL_CALL_ID] == result.data["call"]
    assert len(enriched) == 1  # the inner wrapped call did not enrich


def test_raised_exception_text_is_scrubbed_before_any_file(
    server: Any, otel_spans: InMemorySpanExporter, tmp_path: Path
) -> None:
    """End to end: FastMCP records the exception; the scrubbing file exporter writes none of it."""
    from trw_memory.otel_setup import OtlpJsonFileExporter, ScrubbingSpanExporter

    from trw_mcp.telemetry._tool_span_attrs import OPERATION_NAME, TOOL_CALL_ID

    result = _call(server, "raising_tool", {"text": "t"})
    assert result.is_error
    (span,) = _tool_spans(otel_spans)
    assert span.attributes[OPERATION_NAME] == "execute_tool" and TOOL_CALL_ID in span.attributes
    assert CANARY in (span.status.description or "") or any(e.name == "exception" for e in span.events)
    path = tmp_path / "traces-trw-mcp-1-20260929.jsonl"
    st = tmp_path.stat()
    ScrubbingSpanExporter(OtlpJsonFileExporter(path, (st.st_dev, st.st_ino))).export(
        list(otel_spans.get_finished_spans())
    )
    text = path.read_text()
    assert CANARY not in text
    assert '"error.type"' in text
    assert_no_unkeyed_digest(text.splitlines())


def test_enrichment_is_a_no_op_without_a_recording_span(monkeypatch: pytest.MonkeyPatch) -> None:
    import trw_mcp.telemetry._tool_span_attrs as attrs

    calls: list[object] = []

    class _Span:
        def is_recording(self) -> bool:
            calls.append("is_recording")
            return False

        def set_attribute(self, *_a: object) -> None:  # pragma: no cover - must not run
            calls.append("set_attribute")

    monkeypatch.setattr(attrs.trace, "get_current_span", _Span)
    attrs.enrich_tool_span("run-1", "abcd1234")
    attrs.set_dedup_action("store")
    assert calls == ["is_recording", "is_recording"]


def test_enrichment_fault_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import trw_mcp.telemetry._tool_span_attrs as attrs

    def boom() -> object:
        raise RuntimeError("x")

    monkeypatch.setattr(attrs.trace, "get_current_span", boom)
    attrs.enrich_tool_span("run-1", "abcd1234")
    attrs.set_dedup_action("skip")


def test_invalid_ids_and_actions_are_omitted(otel_spans: InMemorySpanExporter) -> None:
    from trw_mcp.telemetry import _tool_span_attrs as attrs

    with trace.get_tracer("t").start_as_current_span("tools/call x"):
        attrs.enrich_tool_span("bad run/id", "not-hex!")
        attrs.set_dedup_action("delete")
    (span,) = otel_spans.get_finished_spans()
    assert dict(span.attributes or {}) == {attrs.OPERATION_NAME: "execute_tool"}


@pytest.mark.parametrize(
    ("verdict", "expected"), [("skip", "skip"), ("merge", "merge"), ("merge_unresolved", "store"), ("store", "store")]
)
def test_dedup_action_matches_the_decision(
    otel_spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, verdict: str, expected: str
) -> None:
    from types import SimpleNamespace

    import trw_mcp.state.dedup as dedup
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.persistence import FileStateReader, FileStateWriter
    from trw_mcp.telemetry._tool_span_attrs import DEDUP_ACTION
    from trw_mcp.tools import _learning_helpers as helpers

    action = "merge" if verdict == "merge_unresolved" else verdict
    monkeypatch.setattr(
        dedup, "dedup_verdict", lambda *_a, **_k: SimpleNamespace(action=action, existing_id="L-x", similarity=0.99)
    )
    survivor_file = tmp_path / "L-x.yaml"
    survivor = None if verdict == "merge_unresolved" else (survivor_file, {"id": "L-x"})
    monkeypatch.setattr(helpers, "_resolve_merge_survivor", lambda *_a: survivor)

    class _Store:
        def get(self, _id: str) -> None:
            return None

    monkeypatch.setattr(helpers, "_merge_into_store", lambda *_a: (_Store(), {"id": "L-x", "summary": "s"}))
    params = SimpleNamespace(
        learning_id="L-new",
        summary="s",
        detail="d",
        tags=[],
        evidence=[],
        impact=0.5,
        type="pattern",
        confidence="low",
        protection_tier="normal",
        assertions=[],
    )
    with trace.get_tracer("t").start_as_current_span("tools/call trw_learn"):
        out = helpers.check_and_handle_dedup(params, tmp_path, FileStateReader(), FileStateWriter(), TRWConfig())
    assert (out or {}).get("status", "stored") == {"skip": "skipped", "merge": "merged", "store": "stored"}[expected]
    (span,) = otel_spans.get_finished_spans()
    assert span.attributes[DEDUP_ACTION] == expected


def test_dedup_action_absent_when_dedup_disabled(otel_spans: InMemorySpanExporter, tmp_path: Path) -> None:
    from types import SimpleNamespace

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.persistence import FileStateReader, FileStateWriter
    from trw_mcp.tools import _learning_helpers as helpers

    params = SimpleNamespace(learning_id="L-new", summary="s", detail="d")
    with trace.get_tracer("t").start_as_current_span("tools/call trw_learn"):
        helpers.check_and_handle_dedup(
            params, tmp_path, FileStateReader(), FileStateWriter(), TRWConfig(dedup_enabled=False)
        )
    (span,) = otel_spans.get_finished_spans()
    assert "com.trwframework.learn.dedup.action" not in (span.attributes or {})


def test_every_registered_production_tool_is_wrapped() -> None:
    from tests._served_app import served_app
    from tests.conftest import get_tools_sync

    mcp = served_app()
    tools = get_tools_sync(mcp)
    assert tools
    unwrapped = [name for name, tool in tools.items() if not getattr(tool.fn, "__trw_tool_call_wrapped__", False)]
    assert unwrapped == []


@pytest.mark.parametrize("state", ["no_provider", "sdk_disabled"])
def test_results_equal_without_a_provider(state: str, tmp_path: Path) -> None:
    """No-op equivalence: with no SDK provider the wrapped tool returns the same value and nothing raises."""
    code = (
        "import asyncio\n"
        "from fastmcp import FastMCP, Client\n"
        "from trw_mcp.telemetry.tool_call_timing import wrap_tool\n"
        "srv = FastMCP('x')\n"
        "def t(text: str) -> dict:\n"
        "    return {'echo': text}\n"
        "srv.tool(name='t')(wrap_tool(t, tool_name='t'))\n"
        "async def go():\n"
        "    async with Client(srv) as c:\n"
        "        return (await c.call_tool('t', {'text': 'v'})).data\n"
        "print(asyncio.run(go()))\n"
    )
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "TRW_PROJECT_ROOT": str(tmp_path),
        "PYTHONPATH": ":".join(sys.path),
    }
    if state == "sdk_disabled":
        env["OTEL_SDK_DISABLED"] = "true"
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, cwd=tmp_path, capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "{'echo': 'v'}"


def test_retired_otel_keys_warn_and_apply_nothing() -> None:
    from structlog.testing import capture_logs

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.models.config._retired_keys import _reset_warned_keys, warn_unrecognised_config_keys

    _reset_warned_keys()
    with capture_logs() as logs:
        warned = warn_unrecognised_config_keys(["otel_semconv", "otel_capture_messages"], TRWConfig.model_fields)
    assert warned == ["otel_capture_messages", "otel_semconv"]
    assert all(entry["retired"] is True for entry in logs if entry["event"] == "config_key_not_recognised")
    cfg = TRWConfig(otel_semconv="gen_ai", otel_capture_messages=True)  # type: ignore[call-arg]
    assert not hasattr(cfg, "otel_semconv") and not hasattr(cfg, "otel_capture_messages")
    _reset_warned_keys()
