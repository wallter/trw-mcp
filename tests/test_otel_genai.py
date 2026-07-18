"""Tests for OTel GenAI semantic-convention span emission — PRD-INFRA-145.

Uses a recording fake tracer (the opentelemetry SDK is not a project
dependency; only the API is installed). The fake captures span name, the
attribute KEY SET, and span status so the tests can assert FR01-FR09 and the
NFRs without pulling in opentelemetry-sdk's InMemorySpanExporter.

These tests patch/mock only — no filesystem I/O — so the file is classified
``unit`` in conftest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


class _RecordingSpan:
    """Minimal recording stand-in for an OTel Span."""

    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}
        self.status: object | None = None

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def set_status(self, status: object) -> None:
        self.status = status

    def __enter__(self) -> _RecordingSpan:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class _RecordingTracer:
    """Captures the (span_name -> span) of every started span."""

    def __init__(self) -> None:
        self.spans: list[tuple[str, _RecordingSpan]] = []

    def start_as_current_span(self, name: str) -> _RecordingSpan:
        span = _RecordingSpan()
        self.spans.append((name, span))
        return span


def _run_with_recording_tracer(
    fn: Any,
    *args: Any,
    config: Any,
    **kwargs: Any,
) -> _RecordingTracer:
    """Invoke *fn* with a recording tracer + supplied config, return tracer."""
    tracer = _RecordingTracer()
    import opentelemetry.trace as otel_trace

    with (
        patch("trw_mcp.models.config.get_config", return_value=config),
        patch.object(otel_trace, "get_tracer", return_value=tracer),
    ):
        fn(*args, **kwargs)
    return tracer


def _cfg(**overrides: object) -> MagicMock:
    base: dict[str, object] = {
        "otel_enabled": True,
        "otel_semconv": "gen_ai",
        "otel_capture_messages": False,
    }
    base.update(overrides)
    return MagicMock(**base)


@pytest.mark.unit
class TestGenAiToolSpan:
    def test_gen_ai_tool_span_name_and_attrs(self) -> None:
        """FR01: gen_ai mode emits gen_ai.execute_tool + operation/tool name."""
        from trw_mcp.state.otel_wrapper import emit_tool_span

        tracer = _run_with_recording_tracer(
            emit_tool_span,
            "my_tool",
            55.5,
            {"agent_id": "agent-1"},
            config=_cfg(),
        )
        assert len(tracer.spans) == 1
        name, span = tracer.spans[0]
        assert name == "gen_ai.execute_tool"
        assert span.attributes["gen_ai.operation.name"] == "execute_tool"
        assert span.attributes["gen_ai.tool.name"] == "my_tool"
        assert span.attributes["gen_ai.agent.id"] == "agent-1"
        # No custom duration attr in gen_ai mode (FR03).
        assert "tool.duration_ms" not in span.attributes

    def test_attribute_mapping_and_trw_fallback(self) -> None:
        """FR03/FR08: known keys map to gen_ai.*, unknown fall back to trw.*."""
        from trw_mcp.state.otel_wrapper import emit_tool_span

        tracer = _run_with_recording_tracer(
            emit_tool_span,
            "t",
            1.0,
            {"agent_id": "a1", "phase": "deliver", "custom_thing": "x"},
            config=_cfg(),
        )
        _, span = tracer.spans[0]
        assert span.attributes["gen_ai.agent.id"] == "a1"
        # phase has no GenAI equivalent -> vendor-namespaced, never dropped.
        assert span.attributes["trw.phase"] == "deliver"
        assert span.attributes["trw.custom_thing"] == "x"

    def test_error_type_and_span_status(self) -> None:
        """FR06: error_type sets error.type attr + span status ERROR."""
        from opentelemetry.trace import StatusCode

        from trw_mcp.state.otel_wrapper import emit_tool_span

        tracer = _run_with_recording_tracer(
            emit_tool_span,
            "t",
            1.0,
            {"agent_id": "a"},
            config=_cfg(),
            error_type="ValueError",
        )
        _, span = tracer.spans[0]
        assert span.attributes["error.type"] == "ValueError"
        assert span.status == StatusCode.ERROR

    def test_no_error_no_status(self) -> None:
        """No error_type -> no error.type attr, no ERROR status."""
        from trw_mcp.state.otel_wrapper import emit_tool_span

        tracer = _run_with_recording_tracer(emit_tool_span, "t", 1.0, {"agent_id": "a"}, config=_cfg())
        _, span = tracer.spans[0]
        assert "error.type" not in span.attributes
        assert span.status is None

    def test_span_name_and_key_assertions(self) -> None:
        """NFR05: assert the exact attribute KEY SET for the tool span."""
        from trw_mcp.state.otel_wrapper import emit_tool_span

        tracer = _run_with_recording_tracer(
            emit_tool_span, "tool_x", 9.0, {"agent_id": "a", "phase": "plan"}, config=_cfg()
        )
        _, span = tracer.spans[0]
        assert set(span.attributes) == {
            "gen_ai.operation.name",
            "gen_ai.tool.name",
            "gen_ai.agent.id",
            "trw.phase",
        }


@pytest.mark.unit
class TestLegacyByteIdentity:
    def test_legacy_span_shape_unchanged(self) -> None:
        """FR02: legacy mode is byte-identical to the pre-PRD shape."""
        from trw_mcp.state.otel_wrapper import emit_tool_span

        tracer = _run_with_recording_tracer(
            emit_tool_span,
            "my_tool",
            42.0,
            {"agent_id": "agent-1", "phase": "deliver"},
            config=_cfg(otel_semconv="legacy"),
        )
        name, span = tracer.spans[0]
        assert name == "tool.my_tool"
        assert set(span.attributes) == {
            "tool.name",
            "tool.duration_ms",
            "trw.agent_id",
            "trw.phase",
        }
        assert span.attributes["tool.name"] == "my_tool"
        assert span.attributes["tool.duration_ms"] == 42.0
        assert span.attributes["trw.agent_id"] == "agent-1"
        # No gen_ai attrs leak into legacy mode.
        assert not any(k.startswith("gen_ai.") for k in span.attributes)

    def test_legacy_is_default_when_unset(self) -> None:
        """FR02: a config without otel_semconv defaults to legacy behavior."""
        from trw_mcp.state.otel_wrapper import emit_tool_span

        # MagicMock without explicit otel_semconv -> getattr returns a Mock,
        # which _resolve_semconv treats as unknown -> legacy (FR09).
        cfg = MagicMock(otel_enabled=True)
        del cfg.otel_semconv  # force AttributeError path -> getattr default
        tracer = _run_with_recording_tracer(emit_tool_span, "t", 1.0, {"agent_id": "a"}, config=cfg)
        name, _ = tracer.spans[0]
        assert name == "tool.t"


@pytest.mark.unit
class TestAgentAndWorkflowSpans:
    def test_invoke_agent_span(self) -> None:
        """FR04: emit_agent_span -> gen_ai.invoke_agent + agent attrs."""
        from trw_mcp.state.otel_wrapper import emit_agent_span

        tracer = _run_with_recording_tracer(
            emit_agent_span,
            {
                "agent_id": "asst_1",
                "agent_name": "Math Tutor",
                "agent_version": "1.0.0",
                "conversation_id": "conv_9",
            },
            config=_cfg(),
        )
        name, span = tracer.spans[0]
        assert name == "gen_ai.invoke_agent"
        assert set(span.attributes) == {
            "gen_ai.operation.name",
            "gen_ai.agent.id",
            "gen_ai.agent.name",
            "gen_ai.agent.version",
            "gen_ai.conversation.id",
        }
        assert span.attributes["gen_ai.operation.name"] == "invoke_agent"

    def test_invoke_agent_omits_empty_optional(self) -> None:
        """FR04 edge: missing version/conversation are omitted, not emitted empty."""
        from trw_mcp.state.otel_wrapper import emit_agent_span

        tracer = _run_with_recording_tracer(
            emit_agent_span,
            {"agent_id": "asst_1", "agent_name": "Solo", "agent_version": ""},
            config=_cfg(),
        )
        _, span = tracer.spans[0]
        assert "gen_ai.agent.version" not in span.attributes
        assert "gen_ai.conversation.id" not in span.attributes

    def test_invoke_workflow_span(self) -> None:
        """FR05: emit_workflow_span -> gen_ai.invoke_workflow."""
        from trw_mcp.state.otel_wrapper import emit_workflow_span

        tracer = _run_with_recording_tracer(
            emit_workflow_span,
            {"workflow_name": "rca_pipeline"},
            config=_cfg(),
        )
        name, span = tracer.spans[0]
        assert name == "gen_ai.invoke_workflow"
        assert span.attributes["gen_ai.operation.name"] == "invoke_workflow"
        assert span.attributes["gen_ai.workflow.name"] == "rca_pipeline"

    @pytest.mark.parametrize(
        ("emitter_name", "attributes"),
        [
            ("emit_agent_span", {"agent_id": "asst_1"}),
            ("emit_workflow_span", {"workflow_name": "rca_pipeline"}),
        ],
    )
    def test_invocation_error_sets_type_and_status(
        self,
        emitter_name: str,
        attributes: dict[str, object],
    ) -> None:
        """FR06: both invocation span kinds record the error and ERROR status."""
        from opentelemetry.trace import StatusCode

        from trw_mcp.state import otel_wrapper

        emitter = getattr(otel_wrapper, emitter_name)
        tracer = _run_with_recording_tracer(
            emitter,
            attributes,
            config=_cfg(),
            error_type="TimeoutError",
        )
        _, span = tracer.spans[0]
        assert span.attributes["error.type"] == "TimeoutError"
        assert span.status == StatusCode.ERROR

    def test_agent_span_noop_in_legacy_mode(self) -> None:
        """Agent/workflow spans only emit in gen_ai mode."""
        from trw_mcp.state.otel_wrapper import emit_agent_span, emit_workflow_span

        for fn in (emit_agent_span, emit_workflow_span):
            tracer = _run_with_recording_tracer(fn, {"agent_id": "a"}, config=_cfg(otel_semconv="legacy"))
            assert tracer.spans == []


@pytest.mark.unit
class TestMessageAnonymization:
    def test_message_attrs_optin_and_anonymized(self) -> None:
        """FR07/NFR06: opt-in messages are anonymized before egress."""
        from trw_mcp.state.otel_wrapper import emit_tool_span

        raw_in = "contact a@b.com with key sk-AAAAAAAAAAAAAAAAAAAAAAAA now"
        tracer = _run_with_recording_tracer(
            emit_tool_span,
            "t",
            1.0,
            {"agent_id": "a"},
            config=_cfg(otel_capture_messages=True),
            input_messages=raw_in,
            output_messages="reply from b@c.org",
        )
        _, span = tracer.spans[0]
        in_val = str(span.attributes["gen_ai.input.messages"])
        out_val = str(span.attributes["gen_ai.output.messages"])
        assert "<email>" in in_val
        assert "<api_key>" in in_val
        assert "a@b.com" not in in_val
        assert "sk-AAAAAAAAAAAAAAAAAAAAAAAA" not in in_val
        assert "<email>" in out_val
        assert "b@c.org" not in out_val

    def test_messages_off_by_default(self) -> None:
        """FR07/NFR06: default (capture off) emits no message attributes."""
        from trw_mcp.state.otel_wrapper import emit_tool_span

        tracer = _run_with_recording_tracer(
            emit_tool_span,
            "t",
            1.0,
            {"agent_id": "a"},
            config=_cfg(),  # otel_capture_messages defaults False
            input_messages="secret a@b.com",
            output_messages="more",
        )
        _, span = tracer.spans[0]
        assert "gen_ai.input.messages" not in span.attributes
        assert "gen_ai.output.messages" not in span.attributes

    def test_messages_not_emitted_in_legacy_even_if_capture_on(self) -> None:
        """Capture flag is inert in legacy mode (gen_ai gate)."""
        from trw_mcp.state.otel_wrapper import emit_tool_span

        tracer = _run_with_recording_tracer(
            emit_tool_span,
            "t",
            1.0,
            {"agent_id": "a"},
            config=_cfg(otel_semconv="legacy", otel_capture_messages=True),
            input_messages="a@b.com",
        )
        _, span = tracer.spans[0]
        assert "gen_ai.input.messages" not in span.attributes

    def test_message_redacts_project_path(self) -> None:
        """FR07: project_root paths are redacted when supplied."""
        from trw_mcp.state.otel_wrapper import emit_tool_span

        root = Path("/home/me/proj")
        tracer = _run_with_recording_tracer(
            emit_tool_span,
            "t",
            1.0,
            {"agent_id": "a"},
            config=_cfg(otel_capture_messages=True),
            input_messages="failed at /home/me/proj/src/x.py",
            project_root=root,
        )
        _, span = tracer.spans[0]
        val = str(span.attributes["gen_ai.input.messages"])
        assert "<project>" in val
        assert "/home/me/proj" not in val


@pytest.mark.unit
class TestFailOpenAndUnknown:
    def test_unknown_semconv_falls_back(self) -> None:
        """FR09: unknown semconv -> legacy behavior + one warning, no raise."""
        import structlog.testing

        from trw_mcp.state.otel_wrapper import emit_tool_span

        with structlog.testing.capture_logs() as logs:
            tracer = _run_with_recording_tracer(
                emit_tool_span,
                "t",
                1.0,
                {"agent_id": "a"},
                config=_cfg(otel_semconv="weird"),
            )
        name, _ = tracer.spans[0]
        assert name == "tool.t"  # fell back to legacy
        warnings = [r for r in logs if r.get("action") == "otel_semconv_unknown"]
        assert len(warnings) == 1

    def test_noop_when_otel_disabled(self) -> None:
        """No span when otel_enabled is False (all emitters)."""
        from trw_mcp.state.otel_wrapper import (
            emit_agent_span,
            emit_tool_span,
            emit_workflow_span,
        )

        cfg = _cfg(otel_enabled=False)
        with patch("trw_mcp.models.config.get_config", return_value=cfg):
            emit_tool_span("t", 1.0, {"agent_id": "a"})
            emit_agent_span({"agent_id": "a"})
            emit_workflow_span({"workflow_name": "w"})

    def test_fail_open_on_missing_otel(self) -> None:
        """NFR04: missing opentelemetry import is swallowed, no raise."""
        import builtins

        from trw_mcp.state.otel_wrapper import emit_tool_span

        real_import = builtins.__import__

        def blocking_import(name: str, *a: object, **k: object) -> object:
            if name == "opentelemetry" or name.startswith("opentelemetry."):
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *a, **k)

        with (
            patch("trw_mcp.models.config.get_config", return_value=_cfg()),
            patch("builtins.__import__", side_effect=blocking_import),
        ):
            emit_tool_span("t", 1.0, {"agent_id": "a"})  # must not raise

    def test_fail_open_on_config_error(self) -> None:
        """NFR04: a config error is swallowed silently."""
        from trw_mcp.state.otel_wrapper import emit_tool_span

        with patch("trw_mcp.models.config.get_config", side_effect=RuntimeError("boom")):
            emit_tool_span("t", 1.0)  # must not raise


@pytest.mark.unit
class TestNfrAndPackageCleanliness:
    def test_no_event_kwarg(self) -> None:
        """NFR02: the wrapper sources use no reserved structlog event= kwarg."""
        src = Path("src/trw_mcp/state/otel_wrapper.py").read_text()
        src += Path("src/trw_mcp/state/_otel_genai.py").read_text()
        assert "event=" not in src

    def test_no_proprietary_imports(self) -> None:
        """NFR07: changed modules import no proprietary package."""
        proprietary = ("trw_eval", "trw_distill", "trw_autoresearch", "trw_symphony")
        for rel in (
            "src/trw_mcp/state/otel_wrapper.py",
            "src/trw_mcp/state/_otel_genai.py",
        ):
            src = Path(rel).read_text()
            for pkg in proprietary:
                assert pkg not in src

    def test_mapping_table_keys(self) -> None:
        """FR08: the mapping contract maps the documented caller keys."""
        from trw_mcp.state import _otel_genai

        assert _otel_genai.GEN_AI_ATTR_MAP["agent_id"] == "gen_ai.agent.id"
        assert _otel_genai.GEN_AI_ATTR_MAP["conversation_id"] == "gen_ai.conversation.id"
        # phase is intentionally NOT in the map -> trw.phase fallback.
        assert "phase" not in _otel_genai.GEN_AI_ATTR_MAP
        assert _otel_genai.map_attribute_key("phase") == "trw.phase"
