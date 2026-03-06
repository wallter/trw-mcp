"""Optional OpenTelemetry span emission — PRD-INFRA-029-FR05.

Fail-open wrapper: if opentelemetry is not installed or any error occurs,
the function returns silently. Never blocks tool execution.

OTEL is an OPTIONAL dependency — all imports are lazy (inside function body).
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger()


def emit_tool_span(
    tool_name: str,
    duration_ms: float,
    attributes: dict[str, object] | None = None,
) -> None:
    """Emit an OTEL span for a tool invocation.

    Conditional on config.otel_enabled. Uses lazy imports so opentelemetry
    is never loaded unless explicitly enabled.

    Args:
        tool_name: Name of the MCP tool.
        duration_ms: Duration of the tool call in milliseconds.
        attributes: Optional dict of span attributes.
    """
    try:
        from trw_mcp.models.config import get_config

        config = get_config()
        if not config.otel_enabled:
            return

        try:
            from opentelemetry import trace
        except ImportError:
            logger.debug("otel_not_installed", msg="opentelemetry package not available")
            return

        tracer = trace.get_tracer("trw-mcp")
        with tracer.start_as_current_span(f"tool.{tool_name}") as span:
            span.set_attribute("tool.name", tool_name)
            span.set_attribute("tool.duration_ms", duration_ms)
            if attributes:
                for key, value in attributes.items():
                    span.set_attribute(f"trw.{key}", str(value))

    except Exception:
        logger.debug("otel_emit_failed", tool=tool_name)
