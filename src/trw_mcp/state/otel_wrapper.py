"""Optional OpenTelemetry span emission — PRD-INFRA-029-FR05 / PRD-INFRA-145.

Fail-open wrapper: if opentelemetry is not installed or any error occurs,
the function returns silently. Never blocks tool execution.

OTEL is an OPTIONAL dependency — all imports are lazy (inside function body).

PRD-INFRA-145 adds an opt-in ``otel_semconv`` config branch:
  - ``'legacy'`` (default): byte-identical to the historical ``tool.*``/``trw.*``
    span shape — no existing dashboard breaks.
  - ``'gen_ai'``: OpenTelemetry GenAI semantic-convention spans
    (``gen_ai.execute_tool`` / ``invoke_agent`` / ``invoke_workflow``), mapped
    per ``state/_otel_genai.py`` (the FR08 contract). Opt-in PII-bearing
    message-body attributes (default OFF) route through ``telemetry/anonymizer``.

An unknown ``otel_semconv`` value falls back to ``legacy`` with one warning
(FR09). All emission is wrapped in a top-level ``except Exception`` (NFR04).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import structlog

from trw_mcp.state import _otel_genai

logger = structlog.get_logger(__name__)


def _resolve_semconv(value: object) -> str:
    """Return a valid semconv mode, falling back to 'legacy' (FR09).

    Logs exactly one structlog warning (``action='otel_semconv_unknown'``) for
    any value other than 'legacy'/'gen_ai'; never raises.
    """
    if value in ("legacy", "gen_ai"):
        return str(value)
    logger.warning(
        "otel_semconv_unknown",
        action="otel_semconv_unknown",
        value=str(value),
    )
    return "legacy"


def _emit_legacy_tool_span(
    trace: object,
    tool_name: str,
    duration_ms: float,
    attributes: dict[str, object] | None,
) -> None:
    """Legacy span shape — byte-identical to pre-PRD-INFRA-145 (FR02).

    DO NOT MODIFY without updating the FR02 regression-pin test: span name
    ``tool.{tool_name}``, attrs ``tool.name``, ``tool.duration_ms``,
    ``trw.{key}`` (str-cast).
    """
    tracer = trace.get_tracer("trw-mcp")  # type: ignore[attr-defined]
    with tracer.start_as_current_span(f"tool.{tool_name}") as span:
        span.set_attribute("tool.name", tool_name)
        span.set_attribute("tool.duration_ms", duration_ms)
        if attributes:
            for key, value in attributes.items():
                span.set_attribute(f"trw.{key}", str(value))


def _capture_messages_enabled(config: object, semconv: str) -> bool:
    """FR07 gate: messages emitted only when opted in AND in gen_ai mode."""
    return semconv == "gen_ai" and bool(getattr(config, "otel_capture_messages", False))


def _anonymize_message(text: str, project_root: Path | None) -> str:
    """Route a message body through the PII chokepoint (FR07/NFR06)."""
    from trw_mcp.telemetry.anonymizer import redact_paths, strip_pii

    cleaned = strip_pii(text)
    if project_root is not None:
        cleaned = redact_paths(cleaned, project_root)
    return cleaned


def emit_tool_span(
    tool_name: str,
    duration_ms: float,
    attributes: dict[str, object] | None = None,
    *,
    error_type: str | None = None,
    input_messages: str | None = None,
    output_messages: str | None = None,
    project_root: Path | None = None,
) -> None:
    """Emit an OTEL span for a tool invocation.

    Conditional on ``config.otel_enabled``. Uses lazy imports so opentelemetry
    is never loaded unless explicitly enabled. Telemetry-only: never raises,
    never changes tool behavior (NFR03/NFR04).

    Args:
        tool_name: Name of the MCP tool.
        duration_ms: Duration of the tool call in milliseconds.
        attributes: Optional dict of span attributes (caller keys mapped per
            the FR08 table in gen_ai mode; ``trw.{key}`` prefixed in legacy).
        error_type: Error class/category (gen_ai mode: ``error.type`` + span
            status ERROR — FR06).
        input_messages: Opt-in input message body (gen_ai mode + FR07 gate).
        output_messages: Opt-in output message body (gen_ai mode + FR07 gate).
        project_root: When supplied, message bodies also pass through
            ``redact_paths`` (FR07).
    """
    try:
        from trw_mcp.models.config import get_config

        config = get_config()
        if not config.otel_enabled:
            return

        try:
            from opentelemetry import trace
            from opentelemetry.trace import StatusCode
        except ImportError:
            logger.debug("otel_not_installed", msg="opentelemetry package not available")
            return

        semconv = _resolve_semconv(getattr(config, "otel_semconv", "legacy"))
        if semconv == "legacy":
            _emit_legacy_tool_span(trace, tool_name, duration_ms, attributes)
            return

        tracer = trace.get_tracer("trw-mcp")
        with tracer.start_as_current_span(_otel_genai.SPAN_EXECUTE_TOOL) as span:
            _otel_genai.set_tool_attributes(span, tool_name, attributes)
            if error_type:
                _otel_genai.set_error(span, error_type)
                span.set_status(StatusCode.ERROR)
            if _capture_messages_enabled(config, semconv):
                _otel_genai.set_messages(
                    span,
                    input_messages=input_messages,
                    output_messages=output_messages,
                    anonymize=lambda t: _anonymize_message(t, project_root),
                )

    except Exception:  # justified: fail-open, telemetry never blocks tool execution
        logger.debug("otel_emit_failed", tool=tool_name)


def _emit_genai_invocation_span(
    *,
    span_name: str,
    failure_span: str,
    attributes: dict[str, object] | None,
    error_type: str | None,
    set_attributes: Callable[[_otel_genai._Span, dict[str, object] | None], None],
) -> None:
    """Emit the shared gen-ai agent/workflow span lifecycle."""
    try:
        from trw_mcp.models.config import get_config

        config = get_config()
        if not config.otel_enabled:
            return
        if _resolve_semconv(getattr(config, "otel_semconv", "legacy")) != "gen_ai":
            return

        try:
            from opentelemetry import trace
            from opentelemetry.trace import StatusCode
        except ImportError:
            logger.debug("otel_not_installed", msg="opentelemetry package not available")
            return

        tracer = trace.get_tracer("trw-mcp")
        with tracer.start_as_current_span(span_name) as span:
            set_attributes(span, attributes)
            if error_type:
                _otel_genai.set_error(span, error_type)
                span.set_status(StatusCode.ERROR)
    except Exception:  # justified: fail-open, telemetry never blocks execution
        logger.debug("otel_emit_failed", span=failure_span)


def emit_agent_span(
    attributes: dict[str, object] | None = None,
    *,
    error_type: str | None = None,
) -> None:
    """Emit a gen_ai.invoke_agent span (FR04).

    No-op unless ``otel_enabled`` AND ``otel_semconv == 'gen_ai'``. Caller keys
    (``agent_id``/``agent_name``/``agent_version``/``conversation_id``/
    ``provider_name``) are mapped per the FR08 table; unknown keys fall back to
    ``trw.{key}``. Fail-open (NFR04).
    """
    _emit_genai_invocation_span(
        span_name=_otel_genai.SPAN_INVOKE_AGENT,
        failure_span="invoke_agent",
        attributes=attributes,
        error_type=error_type,
        set_attributes=_otel_genai.set_agent_attributes,
    )


def emit_workflow_span(
    attributes: dict[str, object] | None = None,
    *,
    error_type: str | None = None,
) -> None:
    """Emit a gen_ai.invoke_workflow span (FR05).

    No-op unless ``otel_enabled`` AND ``otel_semconv == 'gen_ai'``. Fail-open.
    """
    _emit_genai_invocation_span(
        span_name=_otel_genai.SPAN_INVOKE_WORKFLOW,
        failure_span="invoke_workflow",
        attributes=attributes,
        error_type=error_type,
        set_attributes=_otel_genai.set_workflow_attributes,
    )
