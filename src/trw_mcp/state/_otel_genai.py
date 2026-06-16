"""OpenTelemetry GenAI semantic-convention span mapping — PRD-INFRA-145.

Belongs to the ``state/otel_wrapper.py`` facade. Re-exported there for the
``gen_ai`` semconv branch. This module owns the FR08 mapping contract
(legacy ``trw.*`` attribute keys -> ``gen_ai.*`` registry keys) and the
attribute-construction helpers for tool, agent, and workflow spans.

Spellings verified 2026-06-14 against the live OpenTelemetry GenAI semantic
conventions (open-telemetry/semantic-conventions-genai @ main):
  - span op ``execute_tool`` / ``invoke_agent`` / ``invoke_workflow``
  - ``gen_ai.operation.name`` (Required), ``gen_ai.tool.name`` (Required),
    ``gen_ai.agent.{id,name,version}``, ``gen_ai.conversation.id``,
    ``gen_ai.provider.name``, ``error.type`` (Stable),
    ``gen_ai.input.messages`` / ``gen_ai.output.messages`` (Opt-In).

The conventions are still Development/Experimental upstream, so this whole
path is opt-in (``otel_semconv == 'gen_ai'``). The mapping is centralized so a
future upstream rename is a single-file edit (Risk R1 mitigation).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Callable

# -- Span names (FR01/FR04/FR05) --

SPAN_EXECUTE_TOOL = "gen_ai.execute_tool"
SPAN_INVOKE_AGENT = "gen_ai.invoke_agent"
SPAN_INVOKE_WORKFLOW = "gen_ai.invoke_workflow"

# -- gen_ai.operation.name values (Required attr, FR01/FR04/FR05) --

OP_EXECUTE_TOOL = "execute_tool"
OP_INVOKE_AGENT = "invoke_agent"
OP_INVOKE_WORKFLOW = "invoke_workflow"

# -- Attribute registry keys (FR08 mapping contract) --

ATTR_OPERATION_NAME = "gen_ai.operation.name"
ATTR_TOOL_NAME = "gen_ai.tool.name"
ATTR_AGENT_ID = "gen_ai.agent.id"
ATTR_AGENT_NAME = "gen_ai.agent.name"
ATTR_AGENT_VERSION = "gen_ai.agent.version"
ATTR_CONVERSATION_ID = "gen_ai.conversation.id"
ATTR_PROVIDER_NAME = "gen_ai.provider.name"
ATTR_WORKFLOW_NAME = "gen_ai.workflow.name"
ATTR_ERROR_TYPE = "error.type"
ATTR_INPUT_MESSAGES = "gen_ai.input.messages"
ATTR_OUTPUT_MESSAGES = "gen_ai.output.messages"

# Vendor-namespaced extension prefix for any caller key with no GenAI
# equivalent (FR03 — never dropped).
TRW_EXT_PREFIX = "trw."

# FR08: authoritative caller-attribute-key -> gen_ai registry-key mapping.
# Keys are the bare attribute names supplied by callers (e.g. via
# tools/telemetry.py); values are the GenAI registry attribute keys. Any
# caller key absent from this table falls back to ``trw.{key}`` (FR03).
GEN_AI_ATTR_MAP: dict[str, str] = {
    "agent_id": ATTR_AGENT_ID,
    "agent_name": ATTR_AGENT_NAME,
    "agent_version": ATTR_AGENT_VERSION,
    "conversation_id": ATTR_CONVERSATION_ID,
    "provider_name": ATTR_PROVIDER_NAME,
    "workflow_name": ATTR_WORKFLOW_NAME,
    # ``phase`` deliberately omitted -> falls through to trw.phase (no GenAI
    # equivalent exists; retained as a vendor extension per FR03/FR08).
}


class _Span(Protocol):
    """Structural type for the subset of the OTel Span API we use.

    ``value`` is ``str`` because every attribute this module sets is str-cast
    before egress — that keeps the Protocol assignable from the real OTel
    ``Span`` (whose ``set_attribute`` accepts the wider ``AttributeValue``).
    """

    def set_attribute(self, key: str, value: str) -> None: ...


def map_attribute_key(caller_key: str) -> str:
    """Map a caller-supplied attribute key to its gen_ai/vendor key (FR03/FR08).

    Returns the GenAI registry key when a mapping exists, else the
    vendor-namespaced ``trw.{key}`` fallback (never dropped).
    """
    mapped = GEN_AI_ATTR_MAP.get(caller_key)
    if mapped is not None:
        return mapped
    return f"{TRW_EXT_PREFIX}{caller_key}"


def set_mapped_attributes(span: _Span, attributes: dict[str, object] | None) -> None:
    """Set caller-supplied attributes on *span* using the FR08 mapping.

    Each key is mapped via :func:`map_attribute_key`; values are str-cast for
    mypy --strict + OTel attribute-type safety. Empty/None values are skipped
    so optional attrs (agent.version, conversation.id) are omitted, not emitted
    empty (Test Strategy edge case).
    """
    if not attributes:
        return
    for key, value in attributes.items():
        if value is None or value == "":
            continue
        span.set_attribute(map_attribute_key(key), str(value))


def set_tool_attributes(
    span: _Span,
    tool_name: str,
    attributes: dict[str, object] | None,
) -> None:
    """Set gen_ai.execute_tool span attributes (FR01/FR03)."""
    span.set_attribute(ATTR_OPERATION_NAME, OP_EXECUTE_TOOL)
    span.set_attribute(ATTR_TOOL_NAME, tool_name)
    set_mapped_attributes(span, attributes)


def set_agent_attributes(
    span: _Span,
    attributes: dict[str, object] | None,
) -> None:
    """Set gen_ai.invoke_agent span attributes (FR04)."""
    span.set_attribute(ATTR_OPERATION_NAME, OP_INVOKE_AGENT)
    set_mapped_attributes(span, attributes)


def set_workflow_attributes(
    span: _Span,
    attributes: dict[str, object] | None,
) -> None:
    """Set gen_ai.invoke_workflow span attributes (FR05)."""
    span.set_attribute(ATTR_OPERATION_NAME, OP_INVOKE_WORKFLOW)
    set_mapped_attributes(span, attributes)


def set_error(span: _Span, error_type: str | None) -> None:
    """Set error.type attribute when an error category is present (FR06).

    Span status (ERROR) is set by the caller via the live trace API so this
    helper stays import-free of opentelemetry (keeps the module lazy-safe).
    """
    if error_type:
        span.set_attribute(ATTR_ERROR_TYPE, error_type)


def set_messages(
    span: _Span,
    *,
    input_messages: str | None,
    output_messages: str | None,
    anonymize: Callable[[str], str],
) -> None:
    """Set opt-in gen_ai.input/output.messages, anonymized (FR07/NFR06).

    Each non-empty body is routed through *anonymize* (the PII chokepoint)
    before being attached. Callers MUST gate this on
    ``otel_capture_messages and otel_semconv == 'gen_ai'`` — this helper does
    not re-check the flag, it only guarantees the anonymizer is on the path.
    """
    if input_messages:
        span.set_attribute(ATTR_INPUT_MESSAGES, anonymize(input_messages))
    if output_messages:
        span.set_attribute(ATTR_OUTPUT_MESSAGES, anonymize(output_messages))
