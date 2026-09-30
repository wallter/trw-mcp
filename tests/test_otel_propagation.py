"""Trace context across TRW's own boundaries (PRD-CORE-342 FR05, FR06, FR07 helpers, FR08)."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanContext


async def _current() -> SpanContext:
    return trace.get_current_span().get_span_context()


# --- FR05: the daemon-store thread hop keeps the caller's context (test-first; no code change) ---


def test_run_hop_keeps_the_callers_span(otel_spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch) -> None:
    import trw_mcp.state._project_root_binding as binding
    from trw_mcp.state import _daemon_store

    monkeypatch.setattr(binding, "install_shared", lambda: None)
    with trace.get_tracer("t").start_as_current_span("caller") as span:
        seen = _daemon_store._run(_current())
    assert seen == span.get_span_context()


def test_install_budget_hop_keeps_the_callers_span(
    otel_spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    import trw_mcp.state._project_root_binding as binding
    from trw_mcp.state import _daemon_store

    monkeypatch.setattr(binding, "install_shared", dict)
    with trace.get_tracer("t").start_as_current_span("caller") as span:
        seen = _daemon_store._run(_current())
    assert seen == span.get_span_context()


def test_retire_hop_keeps_the_callers_span(otel_spans: InMemorySpanExporter) -> None:
    from trw_mcp.state import _daemon_store

    seen: list[SpanContext] = []
    done = threading.Event()

    class _Client:
        async def retire(self) -> None:
            seen.append(trace.get_current_span().get_span_context())
            done.set()

    with trace.get_tracer("t").start_as_current_span("caller") as span:
        _daemon_store._retire(_Client())  # type: ignore[arg-type]
    assert done.wait(10)
    assert seen == [span.get_span_context()]


# --- FR06: dispatch child-environment carrier ---


def _dispatch_client() -> Any:
    return "codex"  # the client the existing dispatch env tests use


def test_child_env_carries_traceparent_and_safe_otel_config_only(otel_spans: InMemorySpanExporter) -> None:
    from trw_mcp.dispatch._env import build_runner_env, build_subprocess_env

    source = {
        "PATH": "/bin",
        "TRW_OTEL_ENABLED": "true",
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://user:pass@collector:4318/v1/traces",
        "OTEL_EXPORTER_OTLP_HEADERS": "authorization=Bearer secret",
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS": "x=secret",
        "OTEL_EXPORTER_OTLP_CLIENT_KEY": "/k",
        "OTEL_EXPORTER_OTLP_CERTIFICATE": "/c",
        "OTEL_RESOURCE_ATTRIBUTES": "user.name=me",
        "OTEL_SERVICE_NAME": "host",
        "TRACEPARENT": "00-" + "1" * 32 + "-" + "2" * 16 + "-01",
        "TRACESTATE": "k=v",
        "BAGGAGE": "k=v",
    }
    client = _dispatch_client()
    with trace.get_tracer("t").start_as_current_span("dispatch") as span:
        env = build_subprocess_env(client, source_env=source)
        runner = build_runner_env(client, source_env=source)
        reviewer = build_subprocess_env(client, source_env=source, posture="reviewer")
    ctx = span.get_span_context()
    expected_tp = f"00-{ctx.trace_id:032x}-{ctx.span_id:016x}-{int(ctx.trace_flags):02x}"
    for child in (env, runner, reviewer):
        assert child["TRACEPARENT"] == expected_tp  # the CURRENT span, never the inherited value
        assert child["TRW_OTEL_ENABLED"] == "true"
        assert child["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://localhost:4318"
        for secret in (
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
            "OTEL_EXPORTER_OTLP_HEADERS",
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
            "OTEL_EXPORTER_OTLP_CLIENT_KEY",
            "OTEL_EXPORTER_OTLP_CERTIFICATE",
            "OTEL_RESOURCE_ATTRIBUTES",
            "OTEL_SERVICE_NAME",
            "TRACESTATE",
            "BAGGAGE",
        ):
            assert secret not in child


def test_child_env_has_no_traceparent_without_a_span() -> None:
    from trw_mcp.dispatch._env import build_subprocess_env

    inherited = "00-" + "1" * 32 + "-" + "2" * 16 + "-01"
    env = build_subprocess_env(
        _dispatch_client(),
        source_env={
            "PATH": "/bin",
            "TRACEPARENT": inherited,
            "TRW_OTEL_ENABLED": "true",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318/?token=secret",
        },
    )
    assert env["TRW_OTEL_ENABLED"] == "true"  # the carrier ran ...
    assert "TRACEPARENT" not in env  # ... and never forwards an inherited value
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in env  # a query string can carry a credential


# --- FR07 helpers: validation and links ---


@pytest.mark.parametrize(
    "value",
    [
        "01-" + "1" * 32 + "-" + "2" * 16 + "-01",  # wrong version
        "00-" + "0" * 32 + "-" + "2" * 16 + "-01",  # zero trace id
        "00-" + "1" * 32 + "-" + "0" * 16 + "-01",  # zero span id
        "00-" + "A" * 32 + "-" + "2" * 16 + "-01",  # uppercase
        "00-" + "1" * 32 + "-" + "2" * 16 + "-01-extra",  # trailing data
        None,
        42,
    ],
)
def test_invalid_traceparents_are_rejected(value: object) -> None:
    from trw_mcp.telemetry.otel_propagation import link_from_traceparent, valid_traceparent

    assert valid_traceparent(value) is None
    assert link_from_traceparent(value) is None


def test_current_traceparent_round_trips_to_a_remote_link(otel_spans: InMemorySpanExporter) -> None:
    from trw_mcp.telemetry.otel_propagation import current_traceparent, link_from_traceparent

    assert current_traceparent() is None
    with trace.get_tracer("t").start_as_current_span("sender") as span:
        value = current_traceparent()
    link = link_from_traceparent(value)
    assert link is not None
    ctx = span.get_span_context()
    assert (link.context.trace_id, link.context.span_id) == (ctx.trace_id, ctx.span_id)
    assert link.context.is_remote is True
    assert len(link.context.trace_state) == 0


# --- FR08: CLI root span ---


def test_cli_root_span_parents_on_traceparent(otel_spans: InMemorySpanExporter) -> None:
    from trw_mcp.telemetry.otel_propagation import cli_root_span

    tp = "00-" + "a1" * 16 + "-" + "b2" * 8 + "-01"
    with cli_root_span("status", {"TRACEPARENT": tp}):
        pass
    (span,) = otel_spans.get_finished_spans()
    assert span.name == "trw-mcp status"
    assert span.kind is trace.SpanKind.INTERNAL
    assert span.parent is not None and span.parent.span_id == int("b2" * 8, 16)
    assert span.context.trace_id == int("a1" * 16, 16)


def test_cli_root_span_invalid_traceparent_is_a_new_root(otel_spans: InMemorySpanExporter) -> None:
    from trw_mcp.telemetry.otel_propagation import cli_root_span

    with cli_root_span("status", {"TRACEPARENT": "garbage"}):
        pass
    (span,) = otel_spans.get_finished_spans()
    assert span.parent is None


def test_cli_root_span_error_has_type_only(otel_spans: InMemorySpanExporter) -> None:
    from trw_mcp.telemetry.otel_propagation import cli_root_span

    boom = ValueError("CANARY-cli-text")
    with pytest.raises(ValueError) as raised, cli_root_span("x", {}):
        raise boom
    assert raised.value is boom
    (span,) = otel_spans.get_finished_spans()
    assert span.attributes["error.type"] == "ValueError"
    assert span.status.status_code is trace.StatusCode.ERROR and span.status.description is None
    assert span.events == ()


@pytest.mark.parametrize(("code", "is_error"), [(0, False), (None, False), (2, True)])
def test_cli_root_span_system_exit(otel_spans: InMemorySpanExporter, code: int | None, is_error: bool) -> None:
    from trw_mcp.telemetry.otel_propagation import cli_root_span

    with pytest.raises(SystemExit), cli_root_span("x", {}):
        raise SystemExit(code)
    (span,) = otel_spans.get_finished_spans()
    assert (span.status.status_code is trace.StatusCode.ERROR) is is_error
    assert ("error.type" in (span.attributes or {})) is is_error


def test_cli_main_wires_setup_and_root_span(otel_spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch) -> None:
    import trw_mcp.telemetry.otel_propagation as prop
    from trw_mcp.server import _cli

    installs: list[object] = []
    seen: list[str] = []
    monkeypatch.setattr(prop, "install_tracing", lambda *a: installs.append(a) or False)
    monkeypatch.setitem(_cli.SUBCOMMAND_HANDLERS, "config-reference", lambda _args: seen.append("ran"))
    monkeypatch.setattr(_cli, "enforce_state_changing_guard", lambda *_a: None)
    monkeypatch.setattr(sys, "argv", ["trw-mcp", "config-reference"])
    monkeypatch.setenv("TRACEPARENT", "00-" + "e5" * 16 + "-" + "f6" * 8 + "-01")
    _cli.main()
    assert seen == ["ran"] and len(installs) == 1
    (span,) = [s for s in otel_spans.get_finished_spans() if s.name.startswith("trw-mcp ")]
    assert span.name == "trw-mcp config-reference" and span.context.trace_id == int("e5" * 16, 16)


def test_install_tracing_swallows_a_setup_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.telemetry import otel_propagation as prop

    def boom(_name: str) -> object:
        raise RuntimeError("x")

    monkeypatch.setattr(importlib, "import_module", boom)
    assert prop.install_tracing(TRWConfig(otel_enabled=True)) is False


def test_reviewer_role_never_writes_the_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.telemetry import otel_propagation as prop

    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    assert prop.install_tracing(TRWConfig(otel_enabled=True)) is False
    assert not (tmp_path / ".trw" / "telemetry").exists()


# --- FR02 call site / NFR01: default off, opt-in writes the file (subprocess: the provider is process-global) ---


def _cli(tmp_path: Path, env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "TRW_OTEL", "TRACEPARENT"))}
    env.update({"TRW_PROJECT_ROOT": str(tmp_path), "MEMORY_DAEMON_AUTOSTART": "false", **env_extra})
    code = (
        "import sys\n"
        "from trw_mcp.telemetry.otel_propagation import cli_root_span, install_tracing\n"
        "ok = install_tracing()\n"
        "with cli_root_span('probe', {'TRACEPARENT': '00-' + 'c3' * 16 + '-' + 'd4' * 8 + '-01'}):\n"
        "    pass\n"
        "from opentelemetry import trace\n"
        "p = trace.get_tracer_provider()\n"
        "getattr(p, 'force_flush', lambda: None)()\n"
        "print('installed' if ok else 'off')\n"
        "print('sdk' if any(m.startswith('opentelemetry.sdk') for m in sys.modules) else 'nosdk')\n"
    )
    (tmp_path / ".trw").mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, "-c", code], env=env, cwd=tmp_path, capture_output=True, text=True, timeout=120, check=False
    )


def test_default_config_installs_nothing_and_writes_nothing(tmp_path: Path) -> None:
    out = _cli(tmp_path, {})
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-2:] == ["off", "nosdk"]
    assert not (tmp_path / ".trw" / "telemetry").exists()


def test_opt_in_writes_one_otlp_json_line_for_the_cli_span(tmp_path: Path) -> None:
    import json

    out = _cli(tmp_path, {"TRW_OTEL_ENABLED": "true"})
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-2] == "installed"
    files = list((tmp_path / ".trw" / "telemetry" / "otel").glob("traces-trw-mcp-*.jsonl"))
    assert len(files) == 1
    doc = json.loads(files[0].read_text().splitlines()[0])
    resource = {a["key"]: a["value"] for a in doc["resourceSpans"][0]["resource"]["attributes"]}
    assert resource["service.name"] == {"stringValue": "trw-mcp"}
    from trw_mcp import __version__

    assert resource["service.version"] == {"stringValue": __version__}  # the process's version, not trw-memory's
    (span,) = doc["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert span["name"] == "trw-mcp probe"
    assert span["traceId"] == "c3" * 16 and span["parentSpanId"] == "d4" * 8
