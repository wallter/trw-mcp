"""Tests for the wrap_tool -> telemetry pipeline enqueue path (F2 fix).

The unified-events file written by ``wrap_tool`` is read by NO telemetry
sender (the pipeline reads ``pipeline-events.jsonl``, the sender reads
``tool-telemetry.jsonl``), so before this fix every tool instrumented via
``wrap_tool`` alone produced ZERO PostgreSQL rows. This file drives the REAL
``wrap_tool`` wrapper on trivial functions and asserts that, in addition to
the unified emit, the wrapper now enqueues a flat projection to the telemetry
pipeline with the field names the backend's MAPPED_FIELDS expects.

We mock ONLY the pipeline/network boundary (``TelemetryPipeline.enqueue``);
``wrap_tool`` itself is exercised for real.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from trw_mcp.telemetry.constants import EventType, Status
from trw_mcp.telemetry.event_base import ToolCallEvent
from trw_mcp.telemetry.pipeline import TelemetryPipeline
from trw_mcp.telemetry.tool_call_timing import (
    _pipeline_projection,
    clear_pricing_cache,
    wrap_tool,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    clear_pricing_cache()
    yield
    clear_pricing_cache()


@pytest.fixture
def captured_enqueues(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Capture every dict handed to TelemetryPipeline.enqueue.

    Mocks only the pipeline boundary — the singleton's enqueue method — so the
    real wrap_tool/_pipeline_projection/_enqueue_to_pipeline path runs end to
    end. Returns the list captured calls accumulate into.
    """
    captured: list[dict[str, object]] = []

    class _StubPipeline:
        def enqueue(self, event: dict[str, object]) -> None:
            captured.append(dict(event))

    monkeypatch.setattr(
        TelemetryPipeline,
        "get_instance",
        classmethod(lambda cls: _StubPipeline()),
    )
    return captured


class TestWrapToolEnqueuesToPipeline:
    def test_success_call_enqueues_flat_projection(
        self, captured_enqueues: list[dict[str, object]]
    ) -> None:
        def my_tool(x: int) -> int:
            return x + 1

        wrapped = wrap_tool(
            my_tool,
            tool_name="trw_demo_tool",
            session_id_resolver=lambda: "sess-123",
            run_dir_resolver=lambda: None,
            fallback_dir_resolver=lambda: None,
        )

        result = wrapped(41)

        # Return value is unchanged (fail-open / transparent wrapper).
        assert result == 42

        # Exactly one event was enqueued to the pipeline boundary.
        assert len(captured_enqueues) == 1
        event = captured_enqueues[0]

        # Flat projection mirroring @log_tool_call, with REAL values.
        assert event["tool_name"] == "trw_demo_tool"
        assert event["event_type"] == EventType.TOOL_INVOCATION
        assert event["session_id"] == "sess-123"
        assert event["outcome"] == "success"
        assert event["status"] == Status.SUCCESS
        assert event["success"] is True
        # duration is a real non-negative number (wall_ms), not a sentinel.
        assert isinstance(event["duration_ms"], (int, float))
        assert event["duration_ms"] >= 0
        # No error on success.
        assert "error_type" not in event

    def test_error_call_enqueues_and_reraises(
        self, captured_enqueues: list[dict[str, object]]
    ) -> None:
        def boom() -> None:
            raise ValueError("kaboom")

        wrapped = wrap_tool(
            boom,
            tool_name="trw_boom",
            session_id_resolver=lambda: "sess-err",
            run_dir_resolver=lambda: None,
            fallback_dir_resolver=lambda: None,
        )

        # Exception propagates unchanged (fail-open: telemetry doesn't swallow it).
        with pytest.raises(ValueError, match="kaboom"):
            wrapped()

        # Still enqueued exactly one event with error projection.
        assert len(captured_enqueues) == 1
        event = captured_enqueues[0]
        assert event["tool_name"] == "trw_boom"
        assert event["outcome"] == "error"
        assert event["status"] == Status.ERROR
        assert event["success"] is False
        assert event["error_type"] == "ValueError"
        assert event["session_id"] == "sess-err"

    def test_enqueue_failure_does_not_break_wrapped_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pipeline-enqueue error must NOT break the wrapped tool (fail-open)."""

        class _ExplodingPipeline:
            def enqueue(self, event: dict[str, object]) -> None:
                raise RuntimeError("pipeline down")

        monkeypatch.setattr(
            TelemetryPipeline,
            "get_instance",
            classmethod(lambda cls: _ExplodingPipeline()),
        )

        def my_tool() -> str:
            return "ok"

        wrapped = wrap_tool(
            my_tool,
            tool_name="trw_demo",
            session_id_resolver=lambda: "s",
            run_dir_resolver=lambda: None,
            fallback_dir_resolver=lambda: None,
        )

        # The wrapped call still returns normally despite the enqueue blowing up.
        assert wrapped() == "ok"

    def test_pipeline_singleton_reset_after(self) -> None:
        # Defensive: ensure a fresh singleton for any later test in the session,
        # since these tests patched get_instance on the class.
        TelemetryPipeline.reset()


class TestPipelineProjection:
    def test_projection_maps_payload_to_backend_fields(self) -> None:
        ev = ToolCallEvent(
            session_id="sx",
            run_id="run-9",
            payload={
                "tool": "trw_thing",
                "wall_ms": 137,
                "outcome": "success",
                "error_class": "",
            },
        )
        proj = _pipeline_projection(ev)
        assert proj["tool_name"] == "trw_thing"
        assert proj["duration_ms"] == 137
        assert proj["outcome"] == "success"
        assert proj["status"] == Status.SUCCESS
        assert proj["success"] is True
        assert proj["event_type"] == EventType.TOOL_INVOCATION
        assert proj["session_id"] == "sx"
        assert proj["run_id"] == "run-9"
        assert "error_type" not in proj

    def test_projection_error_path_sets_status_and_error_type(self) -> None:
        ev = ToolCallEvent(
            session_id="sx",
            run_id=None,
            payload={
                "tool": "trw_thing",
                "wall_ms": 5,
                "outcome": "error",
                "error_class": "TimeoutError",
            },
        )
        proj = _pipeline_projection(ev)
        assert proj["status"] == Status.ERROR
        assert proj["success"] is False
        assert proj["error_type"] == "TimeoutError"
        assert proj["run_id"] is None
