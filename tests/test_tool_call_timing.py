"""Tests for tool_call_timing — PRD-HPO-MEAS-001 FR-4."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest

from trw_mcp.telemetry.event_base import ToolCallEvent
from trw_mcp.telemetry.tool_call_timing import (
    _usd_cost_estimate,
    build_tool_call_event,
    clear_pricing_cache,
    wrap_tool,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    clear_pricing_cache()
    yield
    clear_pricing_cache()


class TestBuildToolCallEvent:
    def test_basic_construction(self) -> None:
        start = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)
        end = start + timedelta(milliseconds=250)
        ev = build_tool_call_event(
            tool="trw_recall",
            start_ts=start,
            end_ts=end,
            session_id="s1",
        )
        assert isinstance(ev, ToolCallEvent)
        assert ev.session_id == "s1"
        assert ev.payload["tool"] == "trw_recall"
        assert ev.payload["wall_ms"] == 250
        assert ev.payload["outcome"] == "success"

    def test_wall_ms_clamped_to_nonneg(self) -> None:
        start = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)
        end = start - timedelta(milliseconds=5)  # clock skew
        ev = build_tool_call_event(
            tool="x", start_ts=start, end_ts=end, session_id="s1",
        )
        assert ev.payload["wall_ms"] == 0

    def test_includes_pricing_version(self) -> None:
        ev = build_tool_call_event(
            tool="x",
            start_ts=datetime.now(tz=timezone.utc),
            end_ts=datetime.now(tz=timezone.utc),
            session_id="s1",
        )
        assert "pricing_version" in ev.payload
        # Real pricing.yaml ships with a date-stamped version.
        assert ev.payload["pricing_version"]

    def test_propagates_error_class(self) -> None:
        ev = build_tool_call_event(
            tool="x",
            start_ts=datetime.now(tz=timezone.utc),
            end_ts=datetime.now(tz=timezone.utc),
            session_id="s1",
            outcome="error",
            error_class="RuntimeError",
        )
        assert ev.payload["outcome"] == "error"
        assert ev.payload["error_class"] == "RuntimeError"


class TestUsdCostEstimate:
    def test_zero_when_model_unknown(self) -> None:
        assert _usd_cost_estimate(model_id="gpt-5", input_tokens=1000, output_tokens=1000) == 0.0

    def test_zero_when_model_none(self) -> None:
        assert _usd_cost_estimate(model_id=None, input_tokens=1000, output_tokens=1000) == 0.0

    def test_opus_4_7_rate(self) -> None:
        # opus: $0.015/1K in, $0.075/1K out → 1000+1000 = 0.015 + 0.075 = 0.090
        usd = _usd_cost_estimate(model_id="claude-opus-4-7", input_tokens=1000, output_tokens=1000)
        assert usd == pytest.approx(0.090, abs=1e-6)


class TestWrapTool:
    def test_wrapper_returns_original_value(self) -> None:
        def my_tool(a: int, b: int) -> int:
            return a + b

        wrapped = wrap_tool(my_tool)
        assert wrapped(2, 3) == 5

    def test_wrapper_preserves_exception(self) -> None:
        def my_tool() -> int:
            raise RuntimeError("boom")

        wrapped = wrap_tool(my_tool)
        with pytest.raises(RuntimeError, match="boom"):
            wrapped()

    def test_wrapper_uses_explicit_tool_name(self) -> None:
        def anon() -> None:
            return None

        wrapped = wrap_tool(anon, tool_name="named_override")
        wrapped()
        assert wrapped.__name__ == "anon"  # functools.wraps preserves __name__

    def test_wrapper_calls_session_resolver(self) -> None:
        calls: list[bool] = []

        def resolver() -> str:
            calls.append(True)
            return "s42"

        def my_tool() -> int:
            return 1

        wrapped = wrap_tool(my_tool, session_id_resolver=resolver)
        wrapped()
        assert calls == [True]

    def test_wrapper_swallows_resolver_errors(self) -> None:
        def resolver() -> str:
            raise RuntimeError("resolver down")

        def my_tool() -> int:
            return 1

        wrapped = wrap_tool(my_tool, session_id_resolver=resolver)
        # Should NOT raise — wrapped tool's result is returned even if
        # session resolution blows up.
        assert wrapped() == 1
