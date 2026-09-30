"""Trace-context carriers across TRW's own boundaries (PRD-CORE-342 FR02 call site, FR06-FR08).

API-only: the SDK is installed by ``trw_memory.otel_setup.configure_tracing``, which
:func:`install_tracing` imports lazily and only when ``otel_enabled`` is true. Every helper is a no-op
without a provider and never raises. Nothing here reads ``tracestate`` or baggage (SEC-04.2).
"""

from __future__ import annotations

import contextlib
import importlib
import os
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import structlog
from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

if TYPE_CHECKING:
    from opentelemetry.trace import Link

    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)
#: W3C trace context only, whatever OTEL_PROPAGATORS says: never baggage or a vendor format (SEC-04.2).
_W3C = TraceContextTextMapPropagator()

TRACEPARENT_RE = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
# Non-secret OTel config a TRW-side child needs to join the trace (FR06). Headers, client keys,
# certificates, resource attributes, service name, TRACESTATE and BAGGAGE are deliberately absent.
_OTEL_PASSTHROUGH: tuple[str, ...] = (
    "TRW_OTEL_ENABLED",
    "OTEL_SDK_DISABLED",
    "OTEL_TRACES_EXPORTER",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OTEL_SEMCONV_STABILITY_OPT_IN",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
)
_ENDPOINTS = frozenset({"OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"})


def install_tracing(config: TRWConfig | None = None, service_name: str = "trw-mcp") -> bool:
    """Install the SDK provider for this process when ``otel_enabled``; never raises.

    Called once by the application entrypoint (``server._cli``) before tools are served or a CLI verb
    runs. ``config`` defaults to the resolved project config.
    """
    try:
        if config is None:
            from trw_mcp.models.config import get_config

            config = get_config()
        if not config.otel_enabled:
            return False
        from trw_mcp.dispatch._child_marker import dispatched_child_active
        from trw_mcp.state._paths import resolve_trw_dir
        from trw_mcp.state._surface_role import reviewer_role_active

        file_dir: Path | None = resolve_trw_dir() / "telemetry" / "otel"
        if reviewer_role_active() or dispatched_child_active():
            # These roles never write into the project tree (their logging is stderr-only too): only an
            # explicitly chosen network/console exporter is honoured, never the local file default.
            if os.environ.get("OTEL_TRACES_EXPORTER", "").strip().lower() in ("", "trw_file"):
                return False
            file_dir = None
        # Resolved at runtime: an older trw-memory without otel_setup is an ImportError -> no provider.
        setup = importlib.import_module("trw_memory.otel_setup")
        from trw_mcp import __version__

        return bool(setup.configure_tracing(service_name, file_dir, enabled=True, service_version=__version__))
    except Exception:  # trw-fail-silent-allow: False = no provider; telemetry never blocks startup (NFR01)
        logger.warning("otel_setup_failed", reason="trw_mcp_entrypoint")
        return False


def current_traceparent() -> str | None:
    """The current span as a validated W3C ``traceparent``, or None without a valid span."""
    try:
        carrier: dict[str, str] = {}
        _W3C.inject(carrier)
        return valid_traceparent(carrier.get("traceparent"))
    except Exception:  # trw-fail-silent-allow: None IS the no-context answer; telemetry is best effort
        logger.debug("otel_traceparent_failed", exc_info=True)
        return None


def valid_traceparent(value: object) -> str | None:
    """``value`` if it is a version-00 traceparent with non-zero ids, else None."""
    if not isinstance(value, str):
        return None
    match = TRACEPARENT_RE.match(value)
    if match is None or set(match.group(1)) == {"0"} or set(match.group(2)) == {"0"}:
        return None
    return value


def link_from_traceparent(value: object) -> Link | None:
    """A link to the remote span ``value`` names, with an empty trace state; None if invalid."""
    parsed = valid_traceparent(value)
    if parsed is None:
        return None
    from opentelemetry.trace import Link, SpanContext, TraceFlags, TraceState

    _version, trace_id, span_id, flags = parsed.split("-")
    ctx = SpanContext(
        int(trace_id, 16),
        int(span_id, 16),
        is_remote=True,
        trace_flags=TraceFlags(int(flags, 16)),
        trace_state=TraceState(),
    )
    return Link(ctx)


def child_env_carrier(source_env: Mapping[str, str]) -> dict[str, str]:
    """``TRACEPARENT`` for the current span plus the non-secret OTel passthrough (FR06)."""
    env: dict[str, str] = {}
    for name in _OTEL_PASSTHROUGH:
        value = source_env.get(name)
        if value is None:
            continue
        if name in _ENDPOINTS and _has_userinfo(value):
            continue  # a credential in the URL must not reach a third-party CLI
        env[name] = value
    traceparent = current_traceparent()
    if traceparent is not None:
        env["TRACEPARENT"] = traceparent
    return env


def _has_userinfo(url: str) -> bool:
    try:
        parts = urlsplit(url)
        # userinfo or a query string can both carry a collector credential
        return parts.username is not None or parts.password is not None or "@" in parts.netloc or bool(parts.query)
    except ValueError:
        return True


@contextlib.contextmanager
def cli_root_span(verb: str, environ: Mapping[str, str]) -> Iterator[None]:
    """One INTERNAL span ``trw-mcp {verb}`` parented on ``TRACEPARENT`` (FR08); a no-op without a provider.

    Only short-lived CLI verbs use this; ``serve`` never extracts ``TRACEPARENT``. An invalid value
    yields a new root. A failure sets ``error.type`` and ERROR with no description, and the host
    exception propagates unchanged (C-2).
    """
    span: trace.Span | None = None
    try:
        parent = valid_traceparent(environ.get("TRACEPARENT"))
        ctx = _W3C.extract({"traceparent": parent}) if parent else None
        candidate = trace.get_tracer("trw_mcp").start_span(f"trw-mcp {verb}", context=ctx, kind=trace.SpanKind.INTERNAL)
        span = candidate if candidate.is_recording() else None
    except Exception:  # justified: fail-open, a span must never block a CLI verb
        logger.debug("otel_cli_span_failed", exc_info=True)
    if span is None:
        yield
        return
    with trace.use_span(span, end_on_exit=True, record_exception=False, set_status_on_exception=False):
        try:
            yield
        except SystemExit as exc:
            if exc.code not in (None, 0):
                _mark_error(span, exc)
            raise
        except BaseException as exc:
            _mark_error(span, exc)
            raise


def _mark_error(span: trace.Span, exc: BaseException) -> None:
    span.set_attribute("error.type", type(exc).__name__)
    span.set_status(trace.Status(trace.StatusCode.ERROR))
