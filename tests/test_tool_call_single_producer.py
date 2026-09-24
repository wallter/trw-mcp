"""One producer per tool call (PRD-FIX-150): one pipeline row, one run-log row, no BatchSender row.

Before, a tool decorated with the legacy @log_tool_call reached the backend twice through the
pipeline and a third time, mis-mapped, through the BatchSender queue.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import structlog.contextvars

_SRC = Path(__file__).resolve().parents[1] / "src" / "trw_mcp"


@pytest.fixture
def wrapped(
    fake_memory_store: object, tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, Any], list[dict[str, object]]]:
    """A real production tool that used to carry @log_tool_call, a plain tool, the real registration
    pass, and a pipeline that only counts."""
    from fastmcp import FastMCP

    import trw_mcp.telemetry.pipeline as pipeline_mod
    from tests.conftest import get_tools_sync
    from trw_mcp.models.config import TRWConfig, reload_config
    from trw_mcp.server import _tools
    from trw_mcp.tools.learning import register_learning_tools

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_project))
    monkeypatch.chdir(tmp_project)
    reload_config(TRWConfig(telemetry_enabled=True, platform_telemetry_enabled=True))
    rows: list[dict[str, object]] = []

    class _Counting:
        def enqueue(self, event: dict[str, object]) -> None:
            rows.append(dict(event))

    monkeypatch.setattr(pipeline_mod.TelemetryPipeline, "get_instance", classmethod(lambda cls: _Counting()))
    server = FastMCP("single-producer")
    register_learning_tools(server)

    @server.tool()
    def plain_tool(value: int) -> dict[str, object]:
        """Use when testing. Output: the value and the bound call id."""
        return {"value": value, "tool_call_id": structlog.contextvars.get_contextvars().get("tool_call_id")}

    monkeypatch.setattr(_tools, "mcp", server)
    _tools._apply_security_consult_wrapping()
    yield get_tools_sync(server), rows
    reload_config(None)


def _session_rows(project: Path) -> list[dict[str, object]]:
    path = project / ".trw" / "context" / "session-events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


@pytest.mark.parametrize("tool", ["trw_learn", "plain_tool"])
def test_one_pipeline_row_per_call(wrapped: tuple[dict[str, Any], list[dict[str, object]]], tool: str) -> None:
    tools, rows = wrapped
    args: dict[str, object] = {"learning_id": "L-none", "status": "obsolete"} if tool == "trw_learn" else {"value": 1}

    tools[tool].fn(**args)

    assert [r["tool_name"] for r in rows] == [tool]


def test_no_tool_rows_in_batch_sender_queue(
    wrapped: tuple[dict[str, Any], list[dict[str, object]]], tmp_project: Path
) -> None:
    tools, _rows = wrapped

    tools["trw_learn"].fn(learning_id="L-none", status="obsolete")
    tools["plain_tool"].fn(value=2)

    queue = tmp_project / ".trw" / "logs" / "tool-telemetry.jsonl"
    queued = queue.read_text(encoding="utf-8").splitlines() if queue.exists() else []
    assert [line for line in queued if '"tool_call"' in line or '"tool":' in line] == []


def test_wrapper_binds_trace_ids_and_writes_one_run_log_row(
    wrapped: tuple[dict[str, Any], list[dict[str, object]]], tmp_project: Path
) -> None:
    tools, _rows = wrapped

    result = tools["plain_tool"].fn(value=3)

    # Code inside the tool sees the call's id (build/_registration.py relies on it); it is gone after.
    assert result["tool_call_id"]
    assert "tool_call_id" not in structlog.contextvars.get_contextvars()
    [row] = [r for r in _session_rows(tmp_project) if r.get("tool_name") == "plain_tool"]
    assert row["event"] == "tool_call" and row["success"] is True
    assert row["tool_call_id"] == result["tool_call_id"] and row["event_id"]


def test_a_failing_tool_is_recorded_once_as_a_failure(
    wrapped: tuple[dict[str, Any], list[dict[str, object]]], tmp_project: Path
) -> None:
    from trw_mcp.telemetry.tool_call_timing import wrap_tool

    _tools, rows = wrapped

    def boom() -> None:
        raise RuntimeError("no")

    with pytest.raises(RuntimeError):
        wrap_tool(boom, tool_name="failing_tool")()

    assert [r["tool_name"] for r in rows] == ["failing_tool"]
    [row] = [r for r in _session_rows(tmp_project) if r.get("tool_name") == "failing_tool"]
    assert row["success"] is False and row["error_type"] == "RuntimeError"


def test_no_second_per_call_producer_exists_in_source() -> None:
    offenders = [
        p.relative_to(_SRC).as_posix() for p in _SRC.rglob("*.py") if "log_tool_call" in p.read_text(encoding="utf-8")
    ]
    assert offenders == []


# codex-a FIX-150 P1: every telemetry step around the call is fail-open.
@pytest.mark.parametrize(
    ("target", "attr"),
    [
        ("trw_mcp.telemetry.tool_call_timing", "bind_trace_ids"),
        ("trw_mcp.telemetry.tool_call_timing", "unbind_trace_ids"),
        ("trw_mcp.telemetry.tool_call_timing", "emit_tool_call_event"),
        ("trw_mcp.state._learn_stage_timing", "begin"),
        ("trw_mcp.state._learn_stage_timing", "finish"),
        ("trw_mcp.state._learn_stage_timing", "restore"),
    ],
)
def test_a_telemetry_fault_never_changes_the_tool_outcome(
    wrapped: tuple[dict[str, Any], list[dict[str, object]]], monkeypatch: pytest.MonkeyPatch, target: str, attr: str
) -> None:
    import importlib

    from trw_mcp.telemetry.tool_call_timing import wrap_tool

    faults: list[str] = []

    def fault(*_a: object, **_k: object) -> None:
        faults.append(attr)
        raise OSError(f"injected {attr} fault")

    monkeypatch.setattr(importlib.import_module(target), attr, fault)

    def ok(value: int) -> int:
        return value * 2

    def boom() -> None:
        raise ValueError("the tool's own error")

    assert wrap_tool(ok, tool_name="ok_tool")(value=4) == 8
    with pytest.raises(ValueError, match="the tool's own error"):
        wrap_tool(boom, tool_name="boom_tool")()
    assert faults == [attr, attr]  # the fault really fired on both calls
    # A failed unbind leaves the ids bound by design (fail-open); do not leak them into later tests.
    structlog.contextvars.clear_contextvars()


# codex-a FIX-150 P2: the input/output/task-profile trace hashes survive on both records.
def test_trace_hashes_reach_the_event_and_the_run_log_row(
    wrapped: tuple[dict[str, Any], list[dict[str, object]]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import trw_mcp.telemetry._tool_call_emit as emit_mod
    from trw_mcp.telemetry.tool_call_timing import wrap_tool

    run_dir = tmp_path / "task" / "run-1"
    (run_dir / "meta").mkdir(parents=True)
    (run_dir / "meta" / "run.yaml").write_text("task_profile:\n  profile_hash: tp-abc123\n", encoding="utf-8")
    events: list[Any] = []
    monkeypatch.setattr(emit_mod, "_emit_unified", lambda ctx, *, event, run_dir: events.append(event))

    def echo(value: int) -> int:
        return value

    tool = wrap_tool(echo, tool_name="echo_tool", run_dir_resolver=lambda: run_dir)
    tool(value=1)
    tool(value=2)

    first, second = (e.payload for e in events)
    assert first["task_profile_hash"] == "tp-abc123"
    assert first["input_hash"] and first["input_hash"] != second["input_hash"]
    assert first["output_hash"] and first["output_hash"] != second["output_hash"]
    rows = [json.loads(line) for line in (run_dir / "meta" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(r["input_hash"], r["task_profile_hash"]) for r in rows] == [
        (first["input_hash"], "tp-abc123"),
        (second["input_hash"], "tp-abc123"),
    ]


# Cross-vendor FIX-150 P1: an async tool is recorded after its coroutine has run, not before.
def test_an_async_tool_is_recorded_after_it_runs(
    wrapped: tuple[dict[str, Any], list[dict[str, object]]], tmp_project: Path
) -> None:
    import asyncio
    import inspect

    from trw_mcp.telemetry.tool_call_timing import wrap_tool
    from trw_mcp.telemetry.trace_context import stable_payload_hash

    _tools, rows = wrapped

    async def async_ok(value: int) -> dict[str, int]:
        await asyncio.sleep(0)
        return {"value": value}

    async def async_boom() -> None:
        await asyncio.sleep(0)
        raise RuntimeError("late failure")

    ok_tool = wrap_tool(async_ok, tool_name="async_ok")
    boom_tool = wrap_tool(async_boom, tool_name="async_boom")
    assert inspect.iscoroutinefunction(ok_tool) and inspect.iscoroutinefunction(boom_tool)

    assert asyncio.run(ok_tool(value=5)) == {"value": 5}
    with pytest.raises(RuntimeError, match="late failure"):
        asyncio.run(boom_tool())

    by_name = {r.get("tool_name"): r for r in _session_rows(tmp_project)}
    assert by_name["async_ok"]["success"] is True
    # The awaited result is hashed, not the coroutine object.
    assert by_name["async_ok"]["output_hash"] == stable_payload_hash({"tool": "async_ok", "output": {"value": 5}})
    assert by_name["async_boom"]["success"] is False and by_name["async_boom"]["error_type"] == "RuntimeError"
    assert sorted(r["tool_name"] for r in rows) == ["async_boom", "async_ok"]


# Cross-vendor FIX-150 P2: a fault in the local OTEL span step never costs the call its pipeline row.
def test_a_span_fault_still_enqueues_the_pipeline_row(
    wrapped: tuple[dict[str, Any], list[dict[str, object]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    import trw_mcp.state.otel_wrapper as otel
    from trw_mcp.telemetry.tool_call_timing import wrap_tool

    _tools, rows = wrapped

    def broken_span(*_a: object, **_k: object) -> None:
        raise OSError("span exporter down")

    monkeypatch.setattr(otel, "emit_tool_span", broken_span)

    assert wrap_tool(lambda: 1, tool_name="span_fault_tool")() == 1
    assert [r["tool_name"] for r in rows] == ["span_fault_tool"]


# Cross-vendor FIX-150 P2: a failed trace-id bind leaves no half-bound call id behind.
def test_a_failed_trace_bind_leaves_no_call_id(
    wrapped: tuple[dict[str, Any], list[dict[str, object]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    import trw_mcp.telemetry._tool_call_local as local
    from trw_mcp.telemetry.tool_call_timing import wrap_tool

    calls: list[dict[str, object]] = []

    def failing_bind(**kw: object) -> None:
        calls.append(kw)
        raise RuntimeError("contextvar store unavailable")

    before = dict(structlog.contextvars.get_contextvars())
    monkeypatch.setattr(local.structlog.contextvars, "bind_contextvars", failing_bind)

    assert wrap_tool(lambda: 2, tool_name="bind_fault_tool")() == 2
    assert calls and dict(structlog.contextvars.get_contextvars()) == before


# Cross-vendor FIX-150 round 3: a sync callable that returns an awaitable is recorded once it resolves.
def test_a_sync_callable_returning_an_awaitable_is_recorded_after_it_resolves(
    wrapped: tuple[dict[str, Any], list[dict[str, object]]], tmp_project: Path
) -> None:
    import asyncio

    from trw_mcp.telemetry.tool_call_timing import wrap_tool
    from trw_mcp.telemetry.trace_context import stable_payload_hash

    _tools, rows = wrapped

    async def inner(value: int) -> dict[str, int]:
        await asyncio.sleep(0)
        return {"value": value}

    async def inner_boom() -> None:
        await asyncio.sleep(0)
        raise KeyError("late")

    ok = wrap_tool(lambda value: inner(value), tool_name="sync_returns_awaitable")
    boom = wrap_tool(lambda: inner_boom(), tool_name="sync_returns_failing_awaitable")

    before = dict(structlog.contextvars.get_contextvars())
    pending = ok(value=7)
    assert rows == []  # nothing recorded before the awaitable resolves
    assert asyncio.run(pending) == {"value": 7}
    with pytest.raises(KeyError):
        asyncio.run(boom())

    by_name = {r.get("tool_name"): r for r in _session_rows(tmp_project)}
    ok_row, boom_row = by_name["sync_returns_awaitable"], by_name["sync_returns_failing_awaitable"]
    assert ok_row["success"] is True
    assert ok_row["output_hash"] == stable_payload_hash({"tool": "sync_returns_awaitable", "output": {"value": 7}})
    assert boom_row["success"] is False and boom_row["error_type"] == "KeyError"
    assert dict(structlog.contextvars.get_contextvars()) == before  # the caller's context was restored


class _Unprintable:
    def __str__(self) -> str:
        raise RuntimeError("no str")

    def __repr__(self) -> str:
        raise RuntimeError("no repr")


class _UnprintableError(Exception):
    def __str__(self) -> str:
        raise RuntimeError("no str")


# Cross-vendor FIX-150 round 3: a result or exception whose __str__/__repr__ raises is still recorded.
def test_unprintable_results_and_errors_are_still_recorded(
    wrapped: tuple[dict[str, Any], list[dict[str, object]]], tmp_project: Path
) -> None:
    from trw_mcp.telemetry.tool_call_timing import wrap_tool

    _tools, rows = wrapped
    value = _Unprintable()

    def boom() -> None:
        raise _UnprintableError

    assert wrap_tool(lambda: value, tool_name="unprintable_result")() is value
    with pytest.raises(_UnprintableError):
        wrap_tool(boom, tool_name="unprintable_error")()

    by_name = {r.get("tool_name"): r for r in _session_rows(tmp_project)}
    assert by_name["unprintable_result"]["success"] is True and by_name["unprintable_result"]["output_hash"]
    assert by_name["unprintable_error"]["error_type"] == "_UnprintableError"
    assert sorted(r["tool_name"] for r in rows) == ["unprintable_error", "unprintable_result"]
